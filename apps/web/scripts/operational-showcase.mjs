/** Record an actual deployed operational investigation; no routes or responses mocked. */
/* global process, document, window, innerWidth, console */
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { URL } from 'node:url'

const base = process.argv[2] ?? 'http://127.0.0.1:14000'
const output = resolve(process.argv[3] ?? '../../reports/operational-showcase')
const episodeId = process.argv[4]
if (!episodeId) throw new Error('Usage: node apps/web/scripts/operational-showcase.mjs BASE_URL OUTPUT_DIR EPISODE_ID')
mkdirSync(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: resolve(output, 'video'), size: { width: 1440, height: 1000 } }, reducedMotion: 'reduce' })
const page = await context.newPage()
const errors = []
page.on('pageerror', error => errors.push(error.message))
const screenshots = []
const sourceResponses = []
page.on('response', response => {
  const url = new URL(response.url())
  if (/\/api\/v1\/(?:evidence\/|operations\/(?:evidence|handoffs)\/)/.test(url.pathname)) sourceResponses.push({ path: url.pathname + url.search, status: response.status() })
})
const results = { base, episode_id: episodeId, observed_at: new Date().toISOString(), screenshots, accessibility: [], source_responses: sourceResponses, console_errors: errors }
async function shot(name) { await page.waitForTimeout(1400); const path = resolve(output, name + '.png'); await page.screenshot({ path }); screenshots.push(path) }
async function inspectSource(name, close) {
  await page.getByRole('button', { name, exact: true }).first().click()
  await page.getByRole('heading', { name: 'Operational evidence', exact: true }).waitFor()
  await page.getByRole('heading', { name: 'Source record', exact: true }).waitFor()
  await shot('operational-source')
  await page.getByRole('button', { name: close, exact: true }).click()
}
try {
  await page.goto(base)
  await page.getByRole('heading', { name: 'Recorded episodes', exact: true }).waitFor()
  await page.getByRole('link', { name: 'Try demo', exact: true }).waitFor()
  await shot('episode-library')
  const operationalResponse = page.waitForResponse(response => response.url().includes('/api/v1/operations?'), { timeout: 120000 }).catch(error => error)
  await page.goto(base + '/episodes/' + episodeId)
  const capturedResponse = await operationalResponse
  if (capturedResponse instanceof Error) throw capturedResponse
  if (!capturedResponse.ok()) throw new Error('Operations API returned HTTP ' + capturedResponse.status())
  const operations = await capturedResponse.json()
  results.scope = { episode_id: operations.episode_id, snapshot_id: operations.snapshot_id, as_of: operations.as_of, temporal_mode: operations.temporal_mode }
  results.operational_facts = operations.facts.length
  results.review_first = operations.change_reasoning?.changes.filter(change => change.priority === 'review_first').length ?? 0
  results.signals = operations.change_reasoning?.changes.filter(change => change.signal).length ?? 0
  writeFileSync(resolve(output, 'operations-response.json'), JSON.stringify(operations, null, 2) + '\n')
  await page.getByRole('heading', { name: 'Observed dependencies', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Change analysis', exact: true }).waitFor({ timeout: 60000 })
  await shot('investigation-workspace')
  const section = page.getByRole('region', { name: 'Operational context', exact: true })
  await section.scrollIntoViewIfNeeded()
  await page.evaluate(() => { const target = document.querySelector('#operational-context'); window.scrollTo(0, target.getBoundingClientRect().top + window.scrollY - 145) })
  await shot('change-reasoning')
  await inspectSource('Inspect change record', 'Close operational evidence')
  await page.getByRole('button', { name: 'System graph', exact: true }).click()
  await page.getByRole('img', { name: /Operational knowledge graph connecting/ }).waitFor()
  await page.getByRole('heading', { name: 'Operational knowledge graph', exact: true }).scrollIntoViewIfNeeded()
  await page.getByRole('heading', { name: 'Operational knowledge graph', exact: true }).evaluate(element => window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - 145))
  await shot('operational-knowledge-graph')
  const firstReview = operations.change_reasoning?.changes.find(change => change.priority === 'review_first')
  if (firstReview) {
    if (await page.getByLabel('Context service', { exact: true }).inputValue() !== firstReview.service_id) {
      const focusedResponse = page.waitForResponse(response => response.url().includes('/api/v1/operations?') && response.url().includes(firstReview.service_id), { timeout: 120000 }).catch(error => error)
      await page.getByLabel('Context service', { exact: true }).selectOption(firstReview.service_id)
      const focused = await focusedResponse
      if (focused instanceof Error) throw focused
      if (!focused.ok()) throw new Error('Focused operations API returned HTTP ' + focused.status())
    }
    await page.getByLabel('Graph scope', { exact: true }).selectOption('service')
    await page.getByRole('img', { name: /Operational knowledge graph connecting/ }).waitFor()
    await page.getByRole('heading', { name: 'Operational knowledge graph', exact: true }).scrollIntoViewIfNeeded()
    await page.getByRole('heading', { name: 'Operational knowledge graph', exact: true }).evaluate(element => window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - 145))
    await shot('service-deployment-footprint')
  }
  await page.getByRole('button', { name: 'Relationship table', exact: true }).click()
  await page.getByRole('table', { name: 'Operational relationships and their source records' }).waitFor()
  results.relationship_rows = await section.locator('table tbody tr').count()
  await shot('operational-relationships')
  const handoff = page.getByRole('table', { name: 'Operational relationships and their source records' }).getByRole('row').filter({ hasText: 'MESSAGE_FLOW' }).first()
  if (await handoff.count()) {
    await handoff.getByRole('button', { name: 'Inspect relationship record', exact: true }).click()
    await page.getByRole('heading', { name: 'Message handoff evidence', exact: true }).waitFor()
    await page.getByText('Normalized record byte offset', { exact: true }).first().waitFor()
    await shot('message-handoff-evidence')
    const violations = (await new AxeBuilder({ page }).analyze()).violations
    results.accessibility.push({ view: 'message_handoff_evidence', violations })
    if (violations.length) throw new Error('Message handoff evidence accessibility failure')
    await page.getByRole('button', { name: 'Close handoff evidence', exact: true }).click()
  }
  await page.getByRole('button', { name: 'Previous incidents', exact: true }).click()
  await page.getByRole('table', { name: 'What differs from this earlier incident' }).first().waitFor()
  await shot('historical-comparison')
  await page.getByRole('button', { name: 'Earlier source', exact: true }).first().click()
  await page.getByRole('heading', { name: 'Source record', exact: true }).waitFor()
  await shot('historical-source')
  await page.getByRole('button', { name: 'Close operational evidence', exact: true }).click()
  await page.getByRole('button', { name: 'Change analysis', exact: true }).click()
  const signal = page.getByRole('button', { name: 'Inspect observed signal', exact: true }).first()
  if (await signal.count()) {
    await signal.click()
    await page.getByRole('heading', { name: 'Source excerpt', exact: true }).waitFor()
    await shot('measured-signal-source')
    await page.getByRole('button', { name: 'Close evidence', exact: true }).click()
  }
  await page.getByRole('heading', { name: 'Observed dependencies', exact: true }).scrollIntoViewIfNeeded()
  await shot('observed-dependencies')
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.getByRole('button', { name: 'Previous incidents', exact: true }).click()
    await section.scrollIntoViewIfNeeded()
    await page.evaluate(() => { const target = document.querySelector('#operational-context'); window.scrollTo(0, target.getBoundingClientRect().top + window.scrollY - 145) })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    if (overflow) results.overflow_diagnostics = await page.evaluate(() => ({
      viewport_width: innerWidth, document_width: document.documentElement.scrollWidth,
      elements: [...document.querySelectorAll('body *')].map(element => ({
        tag: element.tagName, class_name: typeof element.className === 'string' ? element.className : '',
        text: element.textContent?.slice(0, 80), right: element.getBoundingClientRect().right,
        width: element.getBoundingClientRect().width, overflow: window.getComputedStyle(element).overflowX,
      })).filter(element => element.right > innerWidth + 250),
    }))
    const violations = (await new AxeBuilder({ page }).analyze()).violations
    results.accessibility.push({ width, overflow, violations })
    if (overflow || violations.length) throw new Error(`Accessibility or overflow failure at ${width}px`)
    await shot('history-' + width)
  }
  await page.getByRole('button', { name: 'Toggle theme', exact: true }).click()
  await page.getByRole('button', { name: 'System graph', exact: true }).click()
  await page.getByRole('button', { name: 'Connected graph', exact: true }).click()
  await page.getByLabel('Graph scope', { exact: true }).selectOption('all')
  await page.getByRole('img', { name: /Operational knowledge graph connecting/ }).waitFor()
  await page.getByRole('heading', { name: 'Operational knowledge graph', exact: true }).evaluate(element => window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - 145))
  const darkViolations = (await new AxeBuilder({ page }).analyze()).violations
  results.accessibility.push({ view: 'dark_operational_graph', violations: darkViolations })
  if (darkViolations.length) throw new Error('Dark operational graph accessibility failure')
  await shot('operational-graph-dark')
  if (sourceResponses.some(response => response.status !== 200)) throw new Error('A source inspection returned a non-success response')
  if (errors.length) throw new Error('Page console errors were recorded')
  results.status = 'PASS'
} catch (error) { results.status = 'FAIL'; results.failure = String(error); await page.screenshot({ path: resolve(output, 'failure.png'), fullPage: true }); throw error }
finally {
  const video = page.video()
  await context.close()
  if (video) { const target = resolve(output, 'tracecause-operational-demo.webm'); await video.saveAs(target); results.video = target }
  await browser.close()
  writeFileSync(resolve(output, 'showcase.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify({ status: results.status, output, screenshots: screenshots.length, video: results.video }))
}
