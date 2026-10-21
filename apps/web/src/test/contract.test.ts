import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import type { components } from '../lib/api/generated'

describe('nanosecond JSON boundary', () => {
  it('keeps a timestamp beyond JavaScript safe integers as a decimal string', () => {
    const parser = z.object({ start_ns: z.string().regex(/^[0-9]{1,20}$/) })
    const span: Pick<components['schemas']['NormalizedSpan'], 'start_ns'> = parser.parse(
      JSON.parse('{"start_ns":"9007199254740993"}'),
    )
    expect(span.start_ns).toBe('9007199254740993')
    expect(JSON.stringify(span)).toContain('"9007199254740993"')
  })
})

import { validate } from '../lib/api/client'
import schemas from '../lib/api/schemas.json'
it('builds browser validators from every exported contract without duplicating enum definitions', () => {
  for (const schema of Object.values(schemas)) expect(() => z.fromJSONSchema(schema as Parameters<typeof z.fromJSONSchema>[0])).not.toThrow()
  expect(validate('ApiCapabilities', { public_read_only:true, mutations_enabled:false, config_hash:"a".repeat(64),ranking_modes:["heuristic"],
    generation_modes:['template'], maximum_question_characters:2000, maximum_graph_hops:3,
    maximum_upload_file_bytes:26214400, maximum_upload_bundle_bytes:104857600,
    review_origin:'ai', independent_human_review:false, limitations:[],
  }).public_read_only).toBe(true)
  expect(() => validate('EpisodePage',{ items:[], next_cursor:null, secret:'injected' })).toThrow()
  expect(() => validate('GraphResponse',{ nodes: new Array(81).fill({}) })).toThrow()
})
