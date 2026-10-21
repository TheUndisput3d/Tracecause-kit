import { useEffect, useRef } from 'react'
import cytoscape, { type Core } from 'cytoscape'
import type { Models } from '../../lib/api/client'

export default function KnowledgeCanvas({ nodes, edges, onInspect }: { nodes: Models['KnowledgeNode'][]; edges: Models['KnowledgeEdge'][]; onInspect: (id: string) => void }) {
  const graph = useRef<Core | null>(null)
  const container = useRef<HTMLDivElement>(null), callback = useRef(onInspect)
  useEffect(() => { callback.current = onInspect }, [onInspect])
  useEffect(() => {
    if (!container.current) return
    const columns = { document: 0, chunk: 1, assertion: 2, service: 3, observation: 4 }
    const counts = { document: 0, chunk: 0, assertion: 0, service: 0, observation: 0 }
    const colors = { document: '#7c3aed', chunk: '#a16207', assertion: '#be185d', service: '#087e8b', observation: '#2563eb' }
    const instance = cytoscape({ container: container.current, layout: { name: 'preset' }, elements: [
      ...nodes.map(node => ({ data: { id: node.node_id, label: node.kind + ': ' + node.label.slice(0, 42), color: colors[node.kind], evidence: node.evidence_ids[0] }, position: { x: columns[node.kind] * 210, y: counts[node.kind]++ * 60 } })),
      ...edges.map((edge, index) => ({ data: { id: 'edge:' + index, source: edge.source, target: edge.target, label: edge.relationship, evidence: edge.evidence_ids[0] } })),
    ], style: [
      { selector: 'node', style: { label: 'data(label)', 'background-color': 'data(color)', 'font-size': 13, 'text-wrap': 'wrap', 'text-max-width': '165px', 'text-valign': 'bottom', 'text-margin-y': 7, 'text-background-opacity': 1, 'text-background-color': '#fff', color: '#172b3a' } },
      { selector: 'edge', style: { 'line-color': '#94a3b8', 'target-arrow-color': '#64748b', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', width: 1.5 } },
    ], minZoom: 0.15, maxZoom: 3, wheelSensitivity: 0.25 })
    graph.current = instance
    instance.on('tap', 'node, edge', event => { const id = event.target.data('evidence'); if (id) callback.current(id) })
    instance.fit(undefined, 40)
    return () => { graph.current = null; instance.destroy() }
  }, [nodes, edges])
  const zoom = (factor: number) => { const value = graph.current; if (value) value.zoom({ level: value.zoom() * factor, renderedPosition: { x: value.width() / 2, y: value.height() / 2 } }) }
  return <><div className="toolbar"><button onClick={() => zoom(1.4)}>Zoom evidence in</button><button onClick={() => zoom(1 / 1.4)}>Zoom evidence out</button><button onClick={() => graph.current?.fit(undefined, 40)}>Fit evidence graph</button><small>Drag to pan. Use the relationship table for keyboard source inspection.</small></div><div className="graph knowledge-canvas" ref={container} role="img" aria-label="Knowledge graph of services, observations, documents, assertions and excerpts. Use Evidence relationships for keyboard source inspection." /></>
}
