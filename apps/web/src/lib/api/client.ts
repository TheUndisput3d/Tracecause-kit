import { z } from 'zod'
import type { components } from './generated'
import schemas from './schemas.json'

export type Models = components['schemas']
export type SchemaName = keyof typeof schemas & keyof Models
const validators = new Map<SchemaName, z.ZodType>()
export function validate<N extends SchemaName>(name: N, value: unknown): Models[N] {
  let parser = validators.get(name)
  if (!parser) {
    parser = z.fromJSONSchema(schemas[name] as Parameters<typeof z.fromJSONSchema>[0])
    validators.set(name, parser)
  }
  return parser.parse(value) as Models[N]
}
export class ApiFailure extends Error {
  constructor(readonly status: number, readonly code: string, message: string, readonly requestId?: string) {
    super(message)
  }
}
export async function api<N extends SchemaName>(name: N, path: string, options: RequestInit = {}): Promise<Models[N]> {
  const timeout = AbortSignal.timeout(30000)
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout
  let response: Response
  try { response = await fetch(`/api/v1${path}`, { ...options, signal, credentials: 'same-origin' }) }
  catch (error) {
    if (signal.aborted && !timeout.aborted) throw error
    throw new ApiFailure(0, timeout.aborted ? 'request_timeout' : 'network_unavailable',
      timeout.aborted ? 'The request timed out. Check job status before submitting again.' : 'Could not reach the service. Check the connection and try again.')
  }
  let value: unknown
  try { value = await response.json() }
  catch { throw new ApiFailure(response.status, 'invalid_response', 'The service returned an unreadable response.') }
  if (!response.ok) {
    let parsed: Models['ErrorEnvelope']['error']
    try { parsed = validate('ErrorEnvelope', value).error }
    catch { throw new ApiFailure(response.status, 'invalid_response', 'The service could not complete the request.') }
    throw new ApiFailure(response.status, parsed.code, parsed.message, parsed.request_id)
  }
  try { return validate(name, value) }
  catch { throw new ApiFailure(response.status, 'contract_mismatch', 'The service response does not match this application version. Refresh after checking the server version.') }
}
export function query(params: Record<string, string | number | null | undefined>) {
  const values = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value != null) values.set(key, String(value)) })
  return `?${values}`
}
export function post<N extends SchemaName>(name: N, path: string, token: string, body?: unknown) {
  return api(name, path, { method: 'POST', headers: {
    Authorization: `Bearer ${token}`, 'Idempotency-Key': crypto.randomUUID(), 'Content-Type': 'application/json',
  }, body: body === undefined ? undefined : JSON.stringify(body) })
}
