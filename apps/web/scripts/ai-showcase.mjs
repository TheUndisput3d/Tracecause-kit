/** Record actual local Ollama generation through the deployed UI. No mocked requests. */
/* global process, console, URLSearchParams */
import { chromium, expect } from '@playwright/test'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { URL } from 'node:url'
import { isDeepStrictEqual } from 'node:util'

const [base = 'http://127.0.0.1:14000', outputArg = 'reports/ai-showcase', episodeId, requestedService] = process.argv.slice(2)
const token = process.env.TRACECAUSE_DEMO_TOKEN
if (!token || !episodeId) throw new Error('Supply TRACECAUSE_DEMO_TOKEN and BASE_URL OUTPUT_DIR EPISODE_ID [SERVICE_NAME]. The token is never logged.')
const output = resolve(outputArg)
mkdirSync(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', recordVideo: { dir: resolve(output, 'video'), size: { width: 1440, height: 1000 } } })
const page = await context.newPage()
const errors = [], screenshots = []
page.on('pageerror', error => errors.push(error.message.replaceAll(token, '[redacted]')))
const sourceResponses = []
page.on('response', response => {
  const url = new URL(response.url())
  if (url.pathname.startsWith('/api/v1/evidence/')) sourceResponses.push({ path: url.pathname + url.search, status: response.status() })
})
const report = { base, episode_id: episodeId, observed_at: new Date().toISOString(), screenshots, errors, source_responses: sourceResponses, provider: 'Actual local Ollama; no intercepted responses' }
async function shot(name) { await page.waitForTimeout(400); const path = resolve(output, name + '.png'); await page.screenshot({ path }); screenshots.push(path) }
try {
  await page.goto(base + '/episodes/' + episodeId)
  await page.getByRole('heading', { name: 'Observed dependencies', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Connect operator', exact: true }).click()
  await page.getByLabel('Operator token', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await page.getByRole('button', { name: 'Operator connected', exact: true }).waitFor()
  await page.getByText('Investigation settings', { exact: true }).click()
  await page.getByLabel('Generation', { exact: true }).selectOption('ollama')
  await page.getByLabel('Retrieval method', { exact: true }).selectOption('GRAPH')
  if (requestedService) await page.getByLabel('Symptom service', { exact: true }).selectOption({ label: requestedService })
  else {
    const episode = await (await page.request.get(base + '/api/v1/episodes/' + episodeId)).json()
    const args = new URLSearchParams({ episode_id: episodeId, snapshot_id: episode.active_snapshot_id, as_of: episode.default_as_of ?? episode.event_interval.end, temporal_mode: 'event_time_replay' })
    const graph = await (await page.request.get(base + '/api/v1/graph?' + args)).json()
    const entry = graph.nodes.find(node => !graph.edges.some(edge => edge.target_service_id === node.service_id)) ?? graph.nodes[0]
    if (!entry) throw new Error('No service is available for a real investigation')
    await page.getByLabel('Symptom service', { exact: true }).selectOption(entry.service_id)
  }
  const reviewTime = new Date().toISOString().slice(11, 23)
  await page.getByLabel('Investigation question').fill(`For the incident review at ${reviewTime} UTC, which services should we investigate, and what recorded evidence supports them?`)
  await page.getByRole('heading', { name: 'Replay and question', exact: true }).scrollIntoViewIfNeeded()
  await shot('ai-investigation-request')
  const submission = page.waitForResponse(response => response.url().endsWith('/api/v1/investigations') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Run investigation', exact: true }).click()
  const accepted = await (await submission).json()
  if (!accepted.investigation_id || !accepted.job_id) throw new Error('Investigation submission failed')
  report.investigation_id = accepted.investigation_id
  const jobStates = [], deadline = Date.now() + 180000
  while (Date.now() < deadline) {
    const job = await (await page.request.get(base + '/api/v1/jobs/' + accepted.job_id)).json()
    const state = { status: job.status, phase: job.phase }
    if (!jobStates.length || JSON.stringify(jobStates.at(-1)) !== JSON.stringify(state)) {
      jobStates.push(state)
      if (job.status !== 'SUCCEEDED') {
        const progress = page.getByRole('region', { name: 'Investigation progress', exact: true })
        if (await progress.count()) await progress.scrollIntoViewIfNeeded()
        await shot('ai-job-' + String(job.status).toLowerCase() + '-' + String(job.phase ?? 'queued').replace(/[^a-zA-Z0-9_-]/g, '-'))
      }
    }
    if (job.status === 'SUCCEEDED') break
    if (['FAILED', 'CANCELLED', 'COMPUTE_LIMIT_DENIED'].includes(job.status)) throw new Error('Actual investigation did not succeed: ' + job.status)
    await page.waitForTimeout(1200)
  }
  report.job_states = jobStates
  await expect(page.getByRole('heading', { name: 'Investigation findings', exact: true })).toBeVisible({ timeout: 30000 })
  const result = await (await page.request.get(base + '/api/v1/investigations/' + accepted.investigation_id)).json()
  if (result.result?.generation_origin !== 'ollama' || result.result.fallback_reason) throw new Error('Expected actual Ollama result without fallback')
  report.generation_origin = result.result.generation_origin
  report.fallback_reason = result.result.fallback_reason
  report.timings_ms = result.result.timings_ms
  report.hypotheses = result.result.narrative.hypotheses.length
  report.evidence_count = result.result.evidence.length
  await page.getByRole('heading', { name: 'Investigation findings', exact: true }).evaluate(element => element.scrollIntoView({ block: 'start' }))
  await shot('ai-investigation-findings')
  await page.getByRole('button', { name: 'Summary source 1', exact: true }).click()
  await page.getByRole('heading', { name: 'Source excerpt', exact: true }).waitFor()
  await shot('ai-cited-evidence')
  await page.getByRole('button', { name: 'Close evidence', exact: true }).click()
  await page.goto(base + '/investigations/' + accepted.investigation_id)
  await page.getByRole('heading', { name: 'Saved investigation', exact: true }).waitFor()
  await page.getByRole('link', { name: 'Export JSON', exact: true }).waitFor()
  await shot('ai-saved-result-export')
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Export JSON', exact: true }).click()
  await (await download).saveAs(resolve(output, 'downloaded-investigation.json'))
  const exportResponse = await page.request.get(base + '/api/v1/investigations/' + accepted.investigation_id + '/export')
  if (!exportResponse.ok()) throw new Error('Saved result export was unavailable')
  const exported = await exportResponse.json()
  if (exported.investigation_id !== accepted.investigation_id || exported.result.generation_origin !== 'ollama' || !isDeepStrictEqual(exported.result, result.result)) throw new Error('Saved export differs from actual generated result')
  const downloaded = JSON.parse(readFileSync(resolve(output, 'downloaded-investigation.json'), 'utf8'))
  if (!isDeepStrictEqual(downloaded, exported)) throw new Error('Browser download differs from the saved export API response')
  writeFileSync(resolve(output, 'investigation-export.json'), JSON.stringify(exported, null, 2) + '\n')
  report.export_verified = true
  if (sourceResponses.some(response => response.status !== 200)) throw new Error('A source inspection returned a non-success response')
  if (errors.length) throw new Error('Page errors were recorded')
  report.status = 'PASS'
} catch (error) {
  report.status = 'FAIL'
  report.failure = String(error).replaceAll(token, '[redacted]')
  await page.screenshot({ path: resolve(output, 'failure.png'), fullPage: true })
  // Never attach an unsanitized Playwright error that may include a filled token.
  // eslint-disable-next-line preserve-caught-error
  throw new Error(report.failure)
}
finally {
  const video = page.video()
  await context.close()
  if (video) { report.video = resolve(output, 'tracecause-live-ai-demo.webm'); await video.saveAs(report.video) }
  await browser.close()
  writeFileSync(resolve(output, 'showcase.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify({ status: report.status, output, screenshots: screenshots.length, video: report.video, investigation_id: report.investigation_id }))
}
