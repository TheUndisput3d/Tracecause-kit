// Real read-only KG workflow against the complete Docker application.
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
const require = createRequire(resolve('apps/web/package.json'))
const { chromium, expect } = require('@playwright/test')
const AxeBuilder = require('@axe-core/playwright').default
const [output, url = 'http://127.0.0.1:14000'] = process.argv.slice(2)
if (!output) throw new Error('Supply a new output directory')
mkdirSync(output, { recursive: false })
const episodeId = '03c9ef66-2609-5aec-86fa-9dda41e1c9af'
const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', error => errors.push(error.message))
try {
  await page.goto(`${url}/episodes/${episodeId}`)
  await page.getByRole('button', { name: 'Explore connected evidence' }).click()
  await page.getByLabel('Knowledge focus service').selectOption({ label: 'storefront' })
  await expect(page.getByRole('button', { name: 'Inspect call step 1' }).first()).toBeVisible()
  const section = page.locator('section[aria-label="Knowledge explorer"]')
  await section.screenshot({ path: resolve(output, 'knowledge-graph.png'), style: '.topbar { position: static !important; }' })
  await page.getByRole('button', { name: 'Inspect check quote' }).first().click()
  await expect(page.getByRole('heading', { name: 'Source excerpt' })).toBeVisible()
  await page.screenshot({ path: resolve(output, 'knowledge-source.png') })
  await page.getByRole('button', { name: 'Close evidence' }).click()
  await page.getByRole('button', { name: 'Evidence relationships', exact: true }).click()
  for (const width of [360, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const audit = await new AxeBuilder({ page }).analyze()
    if (audit.violations.length) throw new Error(JSON.stringify(audit.violations))
    await section.screenshot({ path: resolve(output, `knowledge-table-${width}.png`) })
  }
  const finalCutoff = await page.getByLabel('Exact UTC cutoff').inputValue()
  const earlier = new Date(Date.parse(finalCutoff) - 20000).toISOString()
  await page.getByLabel('Exact UTC cutoff').fill(earlier)
  await page.getByRole('button', { name: 'Pin this cutoff' }).click()
  await page.getByLabel('Exact UTC cutoff').fill(finalCutoff)
  await expect(page.getByText(/Pinned:/)).toContainText(earlier.replace('.000Z', 'Z'))
  await expect(page.getByRole('table', { name: 'Dependency changes and source cutoff' })).toBeVisible()
  await page.locator('section[aria-label="Temporal graph comparison"]').screenshot({ path: resolve(output, 'temporal-comparison.png') })
  await page.getByRole('button', { name: 'Inspect current source' }).first().click()
  await expect(page.getByRole('heading', { name: 'Source excerpt' })).toBeVisible()
  if (errors.length) throw new Error(JSON.stringify(errors))
  writeFileSync(resolve(output, 'report.json'), JSON.stringify({ passed: true, url, episode_id: episodeId, real_api: true, graph_check_path: true, quote_inspected: true, temporal_pin_preserved: true, widths: [360, 1440], accessibility_violations: 0, browser_errors: errors }, null, 2) + '\n')
} finally { await browser.close() }
