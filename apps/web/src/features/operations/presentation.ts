import type { Models } from '../../lib/api/client'

export type OperationalDisplayNode = Omit<Models['OperationalNode'], 'kind'> & { kind: Models['OperationalNode']['kind'] | 'observation' }
export type OperationalDisplayEdge = Omit<Models['OperationalEdge'], 'relationship'> & { relationship: Models['OperationalEdge']['relationship'] | Models['GraphEdge']['relationship'] | 'ABOUT_SERVICE' }

/** Stable service lanes reduce crossed lines; coordinates never create graph relationships. */
export function operationalPositions(nodes: OperationalDisplayNode[], edges: OperationalDisplayEdge[]) {
  const columns = { application: 0, incident: 0, team: 0, service: 1, version: 2, deployment: 3, observation: 3, configuration: 4, runtime: 5, host: 6 }
  const services = nodes.filter(node => node.kind === 'service').sort((a, b) => a.label.localeCompare(b.label))
  const owners = new Map(services.map(node => [node.id, node.id]))
  // Only direct service affiliation and the explicit deployment-to-version link determine lanes.
  for (const edge of edges) if (['VERSION_OF', 'INSTANCE_OF', 'CONFIGURES', 'ABOUT_SERVICE'].includes(edge.relationship) && owners.has(edge.target)) owners.set(edge.source, owners.get(edge.target)!)
  for (const edge of edges) if (edge.relationship === 'DEPLOYS' && owners.has(edge.target)) owners.set(edge.source, owners.get(edge.target)!)
  const positions = new Map<string, { x: number; y: number }>()
  let top = 0
  for (const service of services) {
    const members = nodes.filter(node => owners.get(node.id) === service.id)
    const counts = new Map<number, number>()
    for (const member of members) counts.set(columns[member.kind], (counts.get(columns[member.kind]) ?? 0) + 1)
    const height = Math.max(...counts.values()) * 76
    const rows = new Map<number, number>()
    for (const member of members) {
      const column = columns[member.kind], row = rows.get(column) ?? 0
      rows.set(column, row + 1)
      positions.set(member.id, { x: column * 180, y: top + height / 2 + (row - ((counts.get(column) ?? 1) - 1) / 2) * 76 })
    }
    top += height + 24
  }
  const remaining = nodes.filter(node => !positions.has(node.id))
  const counts = new Map<number, number>(), rows = new Map<number, number>()
  for (const node of remaining) counts.set(columns[node.kind], (counts.get(columns[node.kind]) ?? 0) + 1)
  for (const node of remaining) {
    const column = columns[node.kind], row = rows.get(column) ?? 0
    rows.set(column, row + 1)
    positions.set(node.id, { x: column * 180, y: Math.max(0, top - 24) / 2 + (row - ((counts.get(column) ?? 1) - 1) / 2) * 85 })
  }
  return positions
}

/** Join only returned, scoped source facts. This presentation adds no causal edges. */
export function operationalView(data: Models['OperationsResponse'], focus?: string) {
  const nodes = new Map<string, OperationalDisplayNode>(data.nodes.map(node => [node.id, node]))
  const edges: OperationalDisplayEdge[] = [...data.edges]
  for (const edge of data.graph.edges) {
    const source = 'service:' + edge.source_service_id, target = 'service:' + edge.target_service_id
    for (const id of [source, target]) {
      if (!nodes.has(id)) nodes.set(id, { id, kind: 'service', label: data.graph.nodes.find(node => 'service:' + node.service_id === id)?.display_name ?? id.slice(8, 20), evidence_ids: edge.supporting_evidence_ids })
    }
    edges.push({ source, target, relationship: edge.relationship, evidence_ids: edge.supporting_evidence_ids })
  }
  for (const change of data.change_reasoning?.changes ?? []) {
    if (!change.signal) continue
    const id = change.signal.observation_evidence_id, service = 'service:' + change.service_id
    if (!nodes.has(service) || nodes.has(id)) continue
    nodes.set(id, { id, kind: 'observation', label: change.signal.degradation_detected ? 'Latency / error degradation' : change.signal.anomalous ? 'Anomalous measurement' : 'Recorded measurement', evidence_ids: [id] })
    edges.push({ source: id, target: service, relationship: 'ABOUT_SERVICE', evidence_ids: [id] })
  }
  const visible = new Set<string>()
  if (focus) {
    visible.add('service:' + focus)
    for (let step = 0; step < 3; step++) {
      const expandable = (id: string) => visible.has(id) && (!['application', 'host', 'team', 'service'].includes(nodes.get(id)?.kind ?? '') || id === 'service:' + focus)
      const neighbors = edges.filter(edge => !['CALLS', 'MESSAGE_FLOW'].includes(edge.relationship) && (expandable(edge.source) || expandable(edge.target)))
      for (const edge of neighbors) { visible.add(edge.source); visible.add(edge.target) }
    }
  }
  const eligible = [...nodes.values()].filter(node => !focus || visible.has(node.id))
  // Drawing stays bounded even for a maximum-size source bundle; the table keeps all records.
  const shown = eligible.slice(0, 120)
  const eligibleIds = new Set(eligible.map(node => node.id))
  const eligibleEdges = edges.filter(edge => eligibleIds.has(edge.source) && eligibleIds.has(edge.target))
  const shownIds = new Set(shown.map(node => node.id))
  const shownEdges = eligibleEdges.filter(edge => shownIds.has(edge.source) && shownIds.has(edge.target)).slice(0, 240)
  return { nodes: shown, edges: shownEdges, omittedNodes: eligible.length - shown.length, omittedEdges: eligibleEdges.length - shownEdges.length, allNodes: [...nodes.values()], allEdges: edges }
}
