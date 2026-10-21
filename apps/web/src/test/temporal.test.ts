import { describe, expect, it } from 'vitest'
import { compareGraphs, timelineData } from '../features/graph/temporal'
import type { Models } from '../lib/api/client'

const graph: Models['GraphResponse'] = { episode_id: 'e', snapshot_id: 's', as_of: '2026-01-01T00:00:00Z', temporal_mode: 'event_time_replay', nodes: [], edges: [], warnings: [], truncated: false }
const edge: Models['GraphEdge'] = { edge_id: 'ab', source_service_id: 'a', target_service_id: 'b', relationship: 'CALLS', supporting_evidence_ids: ['span:1'] }
describe('temporal graph and signal bounds', () => {
  it('compares endpoints rather than incidental evidence identities', () => {
    expect(compareGraphs({ ...graph, edges: [edge] }, { ...graph, edges: [{ ...edge, edge_id: 'new' }] })?.retained).toHaveLength(1)
    expect(compareGraphs({ ...graph, edges: [edge] }, { ...graph, edges: [{ ...edge, source_service_id: 'b', target_service_id: 'a' }] })?.added).toHaveLength(1)
  })
  it('rejects cross snapshot, episode, temporal mode and backwards comparisons', () => {
    for (const changed of [{ snapshot_id: 'other' }, { episode_id: 'other' }, { as_of: '2025-01-01T00:00:00Z' }, { temporal_mode: 'available_time_replay' as const }]) expect(compareGraphs(graph, { ...graph, ...changed })).toBeNull()
  })
  it('uses one missing point per gap even over decades', () => {
    const point = { end_ns: '10000000000', p95_ms: 10, sample_count: 2, error_count: 1 } as Models['SeriesPoint']
    const data = timelineData([point, { ...point, end_ns: '1900000000000000000', sample_count: 0 }])
    expect(data).toHaveLength(3)
    expect(data[1].p95).toBeNull()
    expect(data[0].errors).toBe(0.5)
    expect(data[2].errors).toBeNull()
    expect(timelineData([])).toEqual([])
  })
})
