import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { api, ApiFailure } from '../lib/api/client'
const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest:'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
it('surfaces a typed safe failure without retrying mutations',async () => {
  let calls = 0
  server.use(http.post('*/api/v1/investigations',() => { calls++;return HttpResponse.json({ error:{code:'queue_full',message:'The operator queue is full.',retryable:true,request_id:'397d1321-eec4-4ea1-8d45-15fe5f230f2c'} },{status:429}) }))
  // Browser fetch resolves relative URLs; the isolated Node test supplies that resolution.
  const original = globalThis.fetch
  globalThis.fetch = (input, init) => original(new URL(String(input),'http://localhost'),init)
  try { await expect(api('InvestigationSubmission','/investigations',{method:'POST'})).rejects.toBeInstanceOf(ApiFailure);expect(calls).toBe(1) }
  finally { globalThis.fetch = original }
})
it('does not show raw HTML returned by an unavailable proxy',async () => {
  server.use(http.get('*/api/v1/config',() => new HttpResponse('<h1>private proxy details</h1>',{status:502})))
  const original = globalThis.fetch
  globalThis.fetch = (input, init) => original(new URL(String(input),'http://localhost'),init)
  try { await expect(api('ApiCapabilities','/config')).rejects.toThrow('unreadable response') }
  finally { globalThis.fetch = original }
})
