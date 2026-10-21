import { describe, expect, it } from 'vitest'
import { operationalPositions, operationalView } from '../features/operations/presentation'
import type { Models } from '../lib/api/client'

const a = 'a'.repeat(64), b = 'b'.repeat(64)
function fixture(): Models['OperationsResponse'] {
  const scope = { episode_id: '00000000-0000-0000-0000-000000000001', snapshot_id: '00000000-0000-0000-0000-000000000002', as_of: '2026-10-19T00:00:00Z', temporal_mode: 'event_time_replay' as const }
  return { ...scope, application_id: 'test', application_name: 'Test application', topology: 'Test only', facts: [], limitations: [], change_reasoning: null,
    history: { reasoning_version: 'operational-history-signature-v1', matches: [], eligible_episodes: 0, omitted_matches: 0, limitations: [] },
    graph: { ...scope, warnings: [], nodes: [a, b].map((id, i) => ({ service_id: id, service_key: id, display_name: ['gateway', 'worker'][i], quality_flags: [] })), edges: [{ edge_id: '00000000-0000-0000-0000-000000000003', source_service_id: a, target_service_id: b, relationship: 'CALLS', supporting_evidence_ids: ['observation:call'] }], truncated: false },
    nodes: [{ id: 'application:test', kind: 'application', label: 'Test', evidence_ids: ['operation:a'] }, { id: 'service:' + a, kind: 'service', label: 'gateway', evidence_ids: ['operation:a'] }, { id: 'service:' + b, kind: 'service', label: 'worker', evidence_ids: ['operation:b'] }, { id: 'runtime:a', kind: 'runtime', label: 'process-a', evidence_ids: ['operation:a'] }, { id: 'host:a', kind: 'host', label: 'local', evidence_ids: ['operation:a'] }],
    edges: [{ source: 'application:test', target: 'service:' + a, relationship: 'HAS_SERVICE', evidence_ids: ['operation:a'] }, { source: 'application:test', target: 'service:' + b, relationship: 'HAS_SERVICE', evidence_ids: ['operation:b'] }, { source: 'runtime:a', target: 'service:' + a, relationship: 'INSTANCE_OF', evidence_ids: ['operation:a'] }, { source: 'runtime:a', target: 'host:a', relationship: 'HOSTED_ON', evidence_ids: ['operation:a'] }],
  }
}
describe('operational graph presentation', () => {
  it('joins returned communication evidence without relabeling it as causality', () => {
    const data = fixture(), view = operationalView(data)
    expect(view.edges.find(edge => edge.relationship === 'CALLS')).toEqual({ source: 'service:' + a, target: 'service:' + b, relationship: 'CALLS', evidence_ids: ['observation:call'] })
    expect(view.nodes).toHaveLength(data.nodes.length)
    expect(data.edges).toHaveLength(4)
  })
  it('keeps the selected service footprint from expanding through a shared application', () => {
    const view = operationalView(fixture(), a)
    expect(view.nodes.map(node => node.id)).toContain('host:a')
    expect(view.nodes.map(node => node.id)).not.toContain('service:' + b)
    expect(view.edges.every(edge => view.nodes.some(node => node.id === edge.source) && view.nodes.some(node => node.id === edge.target))).toBe(true)
  })
  it('bounds drawing while retaining all source relationships for the table', () => {
    const data = fixture()
    for (let i = 0; i < 200; i++) data.nodes.push({ id: 'runtime:' + i, kind: 'runtime', label: 'Runtime ' + i, evidence_ids: ['operation:' + i] })
    const view = operationalView(data)
    expect(view.nodes).toHaveLength(120)
    expect(view.omittedNodes).toBe(85)
    expect(view.allNodes).toHaveLength(205)
  })
  it('aligns a runtime with its explicitly linked service and leaves shared nodes independent', () => {
    const data = fixture(), positions = operationalPositions(data.nodes, data.edges)
    expect(positions.get('runtime:a')?.y).toBe(positions.get('service:' + a)?.y)
    expect(positions.get('service:' + a)?.y).not.toBe(positions.get('service:' + b)?.y)
    expect(positions.size).toBe(data.nodes.length)
  })
})
