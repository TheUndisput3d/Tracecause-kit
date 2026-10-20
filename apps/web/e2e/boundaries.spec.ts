import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
const root = resolve(import.meta.dirname,'../../..')
const fixture = JSON.parse(readFileSync(resolve(root,'reports/phase15-dashboard/fixture.json'),'utf8'))
const episode = fixture.episodes[0]
test('real delayed replay responses never overwrite a newer cursor or trigger computation',async ({ page }) => {
  let writes = 0
  page.on('request',(request) => { if(request.method()==='POST') writes++ })
  await page.goto('/episodes/' + episode.episode_id)
  await expect(page.getByRole('heading',{ name:'Observed dependencies' })).toBeVisible()
  const first = new Date(Date.parse(episode.cutoff)-40000).toISOString(), latest = new Date(Date.parse(episode.cutoff)-10000).toISOString()
  let delayed = false
  await page.route('**/api/v1/graph?**',async (route) => {
    const response = await route.fetch()
    if (new URL(route.request().url()).searchParams.get('as_of') === first) {
      delayed = true
      await new Promise((resolve) => setTimeout(resolve,1200))
    }
    await route.fulfill({ response })
  })
  await page.getByLabel('Exact UTC cutoff').fill(first)
  await expect.poll(() => delayed).toBe(true)
  await page.getByLabel('Exact UTC cutoff').fill(latest)
  await expect(page.getByText('As of ' + latest.replace('.000Z','Z'),{ exact:false })).toBeVisible()
  await page.waitForTimeout(1500) // Allow the deliberately delayed real response to arrive.
  await expect(page.getByText('As of ' + latest.replace('.000Z','Z'),{ exact:false })).toBeVisible()
  await expect(page.getByText('As of ' + first.replace('.000Z','Z'),{ exact:false })).toHaveCount(0)
  expect(writes).toBe(0)
})
test.describe('explicitly simulated UI boundaries; not backend integration evidence',() => {
  test('empty storage and safe dependency failure have recovery messages',async ({ page }) => {
    await page.route('**/api/v1/episodes?**',(route) => route.fulfill({ json:{ items:[],next_cursor:null } }))
    await page.goto('/')
    await expect(page.getByText(/No matching episodes/)).toBeVisible()
    await page.unroute('**/api/v1/episodes?**')
    await page.route('**/api/v1/episodes?**',(route) => route.fulfill({ status:503,json:{ error:{ code:'dependencies_unavailable',message:'Required storage is unavailable.',request_id:crypto.randomUUID(),retryable:true } } }))
    await page.reload()
    await expect(page.getByRole('alert')).toContainText('Required storage is unavailable.')
    await expect(page.getByRole('button',{ name:'Try again' })).toBeVisible()
  })
  test('80 nodes, long labels and graph truncation remain accessible',async ({ page }) => {
    await page.route('**/api/v1/graph?**',async (route) => {
      const response = await route.fetch(), graph = await response.json()
      graph.nodes = Array.from({ length:80 },(_,index) => ({ service_id:String(index).padStart(64,'0'),service_key:String(index).padStart(64,'0'),display_name:`Long service ${index} ${'a'.repeat(180)}`,quality_flags:[] }))
      graph.edges = []
      graph.truncated = true
      graph.warnings = ['graph_truncated']
      await route.fulfill({ json:graph })
    })
    await page.setViewportSize({ width:360,height:740 })
    await page.goto('/episodes/' + episode.episode_id)
    await expect(page.getByText(/Display truncated at 80 services/)).toBeVisible()
    await page.getByText('All visible services (80)',{ exact:true }).click()
    await expect(page.locator('.table-scroll li')).toHaveCount(80)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
  test('malicious document text stays inert and exact quoted text remains inspectable',async ({ page }) => {
    await page.route('**/api/v1/evidence/**',async (route) => {
      const response = await route.fetch(), evidence = await response.json()
      evidence.text = '<img src=x onerror="window.__injected=true"><script>window.__injected=true</script>\n[unsafe](javascript:alert(1))\nSource text remains data.'
      await route.fulfill({ json:evidence })
    })
    await page.goto('/investigations/' + episode.results[0])
    await page.getByRole('button',{ name:'Summary source 1',exact:true }).click()
    await expect(page.getByText('Source text remains data.',{ exact:false }).first()).toBeVisible()
    expect(await page.evaluate(() => '__injected' in window)).toBe(false)
    await expect(page.locator('.source-quote img,.source-quote script')).toHaveCount(0)
    await page.getByText('Exact excerpt text',{ exact:true }).click()
    await expect(page.locator('pre')).toContainText('onerror=')
  })
  test('public replay hides mutations while preserving saved evidence',async ({ page }) => {
    await page.route('**/api/v1/config',async (route) => { const response=await route.fetch(); const config=await response.json(); config.public_read_only=true;config.mutations_enabled=false;config.generation_modes=['template'];await route.fulfill({ json:config }) })
    await page.goto('/admin/imports')
    await expect(page.getByRole('heading',{ name:'Local imports unavailable' })).toBeVisible()
    await expect(page.getByRole('button',{ name:'Connect operator' })).toHaveCount(0)
    await page.goto('/episodes/' + episode.episode_id)
    await expect(page.getByRole('button',{ name:'Run investigation',exact:true })).toBeDisabled()
  })
})

for (const mode of ['insufficient','fallback','ambiguous'] as const) {
  test(`simulated saved-result presentation: ${mode}`,async ({ page }) => {
    await page.route('**/api/v1/investigations/' + episode.results[0],async (route) => {
      const response = await route.fetch(), view = await response.json()
      if (mode === 'insufficient') {
        view.result.narrative.status='insufficient_evidence';view.result.narrative.hypotheses=[]
        view.result.narrative.summary={text:'Insufficient baseline samples for a supported explanation.',evidence_ids:[]}
      } else if (mode === 'fallback') {
        view.result.generation_origin='template';view.result.fallback_reason='compute_limit_denied'
      } else view.result.warnings.push('ambiguous_service_reference')
      await route.fulfill({json:view})
    })
    await page.goto('/investigations/' + episode.results[0])
    await expect(page.getByText(mode === 'insufficient' ? 'insufficient evidence' : mode === 'fallback' ? /Provider fallback: compute_limit_denied/ : 'ambiguous_service_reference',{exact:mode!=='fallback'})).toBeVisible()
  })
}
test('invalid cutoff cannot submit or display the previous graph as current',async ({page}) => {
  await page.goto('/episodes/' + episode.episode_id)
  await expect(page.getByRole('heading',{name:'Observed dependencies'})).toBeVisible()
  await page.getByLabel('Exact UTC cutoff').fill('2026-01-01T02:10:00')
  await expect(page.getByRole('alert')).toContainText('valid UTC cutoff')
  await expect(page.getByRole('button',{name:'Run investigation',exact:true})).toBeDisabled()
  await expect(page.getByRole('heading',{name:'Observed dependencies'})).toHaveCount(0)
})
test('simulated unavailable snapshot shows safe error and retry',async ({page}) => {
  await page.route('**/api/v1/graph?**',(route) => route.fulfill({status:409,json:{error:{code:'snapshot_unavailable',message:'The requested snapshot is not ready.',request_id:crypto.randomUUID(),retryable:false}}}))
  await page.goto('/episodes/' + episode.episode_id)
  await expect(page.getByRole('alert')).toContainText('snapshot is not ready')
  await expect(page.getByRole('button',{name:'Try again'})).toBeVisible()
})
