import { createRequire } from 'node:module'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
const require = createRequire(resolve('apps/web/package.json'))
const { chromium } = require('@playwright/test')
const [reportPath, output] = process.argv.slice(2)
if (!reportPath || !output) throw new Error('Supply smoke report and new output directory')
const fixture = JSON.parse(readFileSync(reportPath, 'utf8'))
mkdirSync(output, { recursive: false })
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ ignoreHTTPSErrors: fixture.tls_ca_verified })
const errors = [], requests = []
page.on('pageerror', error => errors.push(error.message))
page.on('request', request => requests.push(request.url()))
try {
  await page.goto(fixture.url)
  await page.getByRole('heading', { name: 'Recorded episodes' }).waitFor()
  if (await page.getByRole('button', { name: 'Connect operator' }).count()) throw new Error('Public operator control exposed')
  await page.screenshot({ path: resolve(output, 'overview.png'), fullPage: true })
  await page.goto(`${fixture.url}/episodes/${fixture.episode_id}`)
  await page.getByRole('heading', { name: 'Observed dependencies' }).waitFor()
  await page.goto(`${fixture.url}/investigations/${fixture.investigation_id}`)
  await page.getByRole('heading', { name: 'Saved investigation' }).waitFor()
  await page.locator('.result .citation:visible').first().click()
  await page.getByRole('heading', { name: 'Source excerpt' }).waitFor()
  await page.screenshot({ path: resolve(output, 'saved-citation.png'), fullPage: true })
  const external = requests.filter(url => new URL(url).origin !== new URL(fixture.url).origin)
  if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }))
  writeFileSync(resolve(output, 'result.json'), JSON.stringify({ passed: true, errors, external, request_count: requests.length, tls_verified_separately_by_smoke: fixture.tls_ca_verified }, null, 2))
} finally {
  await browser.close()
}
