// Actual operator UI against compose.full.yaml; no intercepted/mock responses.
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
const require = createRequire(resolve('apps/web/package.json'))
const { chromium, expect } = require('@playwright/test')
const [output, url = 'http://127.0.0.1:14000', generation = 'template'] = process.argv.slice(2)
if (!output) throw new Error('Supply a new output directory')
if (!['template', 'ollama'].includes(generation)) throw new Error('Unknown generation mode')
mkdirSync(output, { recursive: false })
const token = execFileSync('docker', ['compose', '-f', 'compose.full.yaml', 'exec', '-T', 'api', 'cat', '/run/secrets/operator_token'], { encoding: 'utf8' }).trim()
const episode = (await (await fetch(url + '/api/v1/episodes')).json()).items[0]
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = [], external = []
page.on('pageerror', error => errors.push(error.message))
page.on('request', request => { if (new URL(request.url()).origin !== new URL(url).origin) external.push(request.url()) })
try {
  await page.goto(url)
  await page.getByRole('heading', { name: 'Recorded episodes' }).waitFor()
  await page.screenshot({ path: resolve(output, 'overview.png') })
  await page.goto(`${url}/episodes/${episode.episode_id}`)
  await page.getByRole('heading', { name: 'Observed dependencies' }).waitFor()
  await page.getByRole('button', { name: 'Connect operator' }).click()
  await page.getByLabel('Operator token', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await page.getByText('Investigation settings', { exact: true }).click()
  await expect(page.getByLabel('Generation', { exact: true }).locator('option')).toHaveText(['template', 'ollama'])
  await page.getByLabel('Generation', { exact: true }).selectOption('ollama')
  await expect(page.getByLabel('Generation', { exact: true })).toHaveValue('ollama')
  await page.getByLabel('Generation', { exact: true }).selectOption(generation)
  await page.getByLabel('Retrieval method').selectOption('GRAPH')
  await page.getByLabel('Investigation question').fill('Review the recorded measurements. Docker browser check ' + crypto.randomUUID())
  await page.screenshot({ path: resolve(output, 'workbench.png') })
  const submission = page.waitForResponse(response => response.url().endsWith('/api/v1/investigations') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Run investigation', exact: true }).click()
  const submitted = await (await submission).json()
  await expect(page.getByRole('heading', { name: 'Investigation findings' })).toBeVisible({ timeout: 90000 })
  if (generation === 'ollama') {
    await expect(page.getByText('AI selected the facts for this summary. Values and citations come from the recorded evidence.')).toBeVisible()
    const view = await (await page.request.get(`${url}/api/v1/investigations/${submitted.investigation_id}`)).json()
    if (view.result.generation_origin !== 'ollama' || view.result.fallback_reason) throw new Error('Unexpected model fallback')
  }
  await page.getByRole('button', { name: 'Summary source 1', exact: true }).click()
  await page.getByRole('heading', { name: 'Source excerpt' }).waitFor()
  await page.screenshot({ path: resolve(output, 'citation.png') })
  if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }))
  writeFileSync(resolve(output, 'report.json'), JSON.stringify({ passed: true, url, investigation_id: submitted.investigation_id, operator_login: true, generation_mode: generation, ollama_option_available: true, errors, external }, null, 2))
} finally { await browser.close() }
