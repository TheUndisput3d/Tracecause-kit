import { useEffect, useRef } from 'react'
import cytoscape, { type Core } from 'cytoscape'
import { Maximize2, Minus, Plus } from 'lucide-react'
import type { OperationalDisplayNode, OperationalDisplayEdge } from './presentation'
import { operationalPositions } from './presentation'

const palette: Record<OperationalDisplayNode['kind'], string> = { application: '#375e69', service: '#166e5f', deployment: '#8a5032', version: '#5b5893', configuration: '#966411', runtime: '#26698b', host: '#496472', team: '#85617d', incident: '#9d4947', observation: '#b14e35' }
export default function OperationalGraph({ nodes, edges, onInspect }: { nodes: OperationalDisplayNode[]; edges: OperationalDisplayEdge[]; onInspect: (id: string) => void }) {
  const container = useRef<HTMLDivElement>(null), graph = useRef<Core | null>(null), callback = useRef(onInspect)
  useEffect(() => { callback.current = onInspect }, [onInspect])
  useEffect(() => {
    if (!container.current) return
    const positions = operationalPositions(nodes, edges)
    const instance = cytoscape({ container: container.current, layout: { name: 'preset' }, elements: [
      ...nodes.map(node => ({ data: { id: node.id, label: node.kind.toUpperCase() + '\n' + (node.label.length > 22 ? node.label.slice(0, 19) + '…' : node.label), color: palette[node.kind], evidence: node.evidence_ids[0] }, position: positions.get(node.id) })),
      ...edges.map((edge, index) => ({ data: { id: 'operational-edge:' + index, source: edge.source, target: edge.target, kind: edge.relationship, label: edge.relationship.replaceAll('_', ' ').toLowerCase(), evidence: edge.evidence_ids[0] } })),
    ], style: [
      { selector: 'node', style: { shape: 'round-rectangle', width: 145, height: 54, 'background-color': '#ffffff', 'border-width': 1.5, 'border-color': 'data(color)', label: 'data(label)', color: 'data(color)', 'font-size': 12, 'font-family': 'system-ui, sans-serif', 'text-wrap': 'wrap', 'text-max-width': '135px', 'text-valign': 'center', 'line-height': 1.5 } },
      { selector: 'edge', style: { label: 'data(label)', 'font-size': 8, 'text-background-color': '#f8faf9', 'text-background-opacity': 1, 'text-background-padding': '3px', color: '#5d756d', 'line-color': '#afc4bc', 'target-arrow-color': '#93aca1', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': .75, width: 1.2 } },
      { selector: 'edge[kind = "CALLS"], edge[kind = "MESSAGE_FLOW"]', style: { 'curve-style': 'unbundled-bezier', 'control-point-distances': [70], 'control-point-weights': [.5] } },
      { selector: 'edge[kind = "MESSAGE_FLOW"]', style: { 'line-style': 'dashed', 'line-color': '#8b7ba5', 'target-arrow-color': '#8b7ba5', color: '#77648e' } },
      { selector: 'edge[kind = "CALLS"]', style: { width: 2, 'line-color': '#4d8877', 'target-arrow-color': '#4d8877' } },
      { selector: ':selected', style: { 'border-width': 3, 'border-color': '#bf7d20', 'line-color': '#bf7d20', 'target-arrow-color': '#bf7d20' } },
    ], minZoom: .15, maxZoom: 2.5, wheelSensitivity: .25 })
    graph.current = instance
    instance.on('tap', 'node, edge', event => { const evidence = event.target.data('evidence'); if (evidence) callback.current(evidence) })
    instance.fit(undefined, 35)
    return () => { graph.current = null; instance.destroy() }
  }, [nodes, edges])
  const zoom = (factor: number) => { const value = graph.current; if (value) value.zoom({ level: value.zoom() * factor, renderedPosition: { x: value.width() / 2, y: value.height() / 2 } }) }
  return <><div className="operational-graph-wrap"><div className="graph operational-canvas" ref={container} role="img" aria-label="Operational knowledge graph connecting source-backed deployments, versions, services and infrastructure. Use Relationship table for keyboard source inspection." /><div className="graph-toolbox"><button aria-label="Zoom operational graph in" onClick={() => zoom(1.3)}><Plus size={16} /></button><button aria-label="Zoom operational graph out" onClick={() => zoom(1 / 1.3)}><Minus size={16} /></button><button aria-label="Fit operational graph" onClick={() => graph.current?.fit(undefined, 35)}><Maximize2 size={16} /></button></div></div><div className="graph-kind-legend">{Object.entries(palette).filter(([kind]) => nodes.some(node => node.kind === kind)).map(([kind, color]) => <span key={kind}><i style={{ background: color }} />{kind}</span>)}</div></>
}
