import { useEffect, useRef, useState } from 'react'
import cytoscape, { type Core } from 'cytoscape'
import type { Models } from '../../lib/api/client'
import type { Citation } from '../evidence/EvidenceDrawer'
function fitGraph(instance: Core) {
  instance.fit(undefined, 35)
  // A small topology should keep readable card-sized nodes rather than fill the canvas with them.
  if (instance.zoom() > 1.25) { instance.zoom(1.25); instance.center() }
}
function arrangeGraph(instance: Core) {
  if (!instance.nodes().length) return
  instance.layout({ name: 'breadthfirst', directed: true, padding: 45, spacingFactor: .95, animate: false, fit: false, transform: (_node, position) => instance.width() > 650 ? { x: position.y, y: position.x } : position }).run()
  fitGraph(instance)
}
export function GraphLegend() { return <p className="muted">Arrows = observed CALLS · Outline = selected hypothesis support · Dependencies do not prove causality.</p> }
export function GraphTable({ graph, onOpen, onSelect }: { onSelect: (id:string) => void; graph: Models['GraphResponse']; onOpen: (c: Citation) => void }) {
  const [reverse, setReverse] = useState(false)
  const names = new Map(graph.nodes.map((n) => [n.service_id, n.display_name]))
  return <div className="table-scroll"><table><caption>Observed service dependencies</caption><thead><tr><th><button onClick={() => setReverse(!reverse)}>Caller {reverse ? '↓' : '↑'}</button></th><th>Callee</th><th>Source observations</th></tr></thead><tbody>{[...graph.edges].sort((a,b) => (reverse ? -1 : 1) * (names.get(a.source_service_id) ?? '').localeCompare(names.get(b.source_service_id) ?? '')).map((edge) => <tr key={edge.edge_id}><td>{names.get(edge.source_service_id) ?? edge.source_service_id}</td><td>{names.get(edge.target_service_id) ?? edge.target_service_id}</td><td>{edge.supporting_evidence_ids.map((id, index) => <button className="citation" key={id} onClick={() => onOpen({ id, snapshot: graph.snapshot_id, cutoff: graph.as_of, temporal: graph.temporal_mode, from: 'Observed call' })}>Observation {index + 1}</button>)}</td></tr>)}</tbody></table>{!graph.edges.length && <p>No eligible observed calls at this cutoff.</p>}<details><summary>All visible services ({graph.nodes.length})</summary><ul>{graph.nodes.map((node) => <li key={node.service_id}><button className="text-button" onClick={() => onSelect(node.service_id)}>{node.display_name}</button> {node.quality_flags.join(', ')}</li>)}</ul></details></div>
}
export function DependencyGraph({ graph, selected, paths, onOpen, series }: { series?: Models['SeriesResponse']; graph: Models['GraphResponse']; selected: string | null; paths: Models['GraphPath'][]; onOpen: (c: Citation) => void }) {
  const container = useRef<HTMLDivElement>(null), cy = useRef<Core | null>(null), manualCamera = useRef(false)
  const [inspected, setInspected] = useState<string | null>(null)
  const node = graph.nodes.find((item) => item.service_id === inspected)
  const point = series?.points.filter((item) => item.service_id === inspected).at(-1)
  const callback = useRef(onOpen)
  const [table, setTable] = useState(matchMedia('(max-width: 767px)').matches)
  useEffect(() => { callback.current = onOpen }, [onOpen])
  useEffect(() => {
    if (!container.current || table) return
    const instance = cytoscape({ container: container.current, elements: [], style: [
      { selector: 'node', style: { shape: 'round-rectangle', width: 140, height: 52, 'background-color': '#ffffff', 'border-color': '#91b6a7', 'border-width': 1.5, label: 'data(label)', color: '#204439', 'font-size': 12, 'font-weight': 600, 'font-family': 'system-ui, sans-serif', 'text-wrap': 'ellipsis', 'text-max-width': '126px', 'text-valign': 'center' } },
      { selector: 'edge', style: { width: 1.5, 'line-color': '#91aaa3', 'target-arrow-color': '#648b7e', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': .85 } },
      { selector: '.support', style: { 'border-width': 4, 'border-color': '#d97706', 'line-color': '#d97706', 'target-arrow-color': '#d97706' } },
      { selector: 'edge.support', style: { width: 4 } },
    ], minZoom: 0.25, maxZoom: 3, wheelSensitivity: 0.3 })
    instance.on('tap', 'node', (event) => setInspected(event.target.id()))
    instance.on('tap', 'edge', (event) => { const data = event.target.data(); callback.current({ id: data.evidence, snapshot: data.snapshot, cutoff: data.cutoff, temporal: data.temporal, from: 'Observed call' }) })
    manualCamera.current = false
    instance.on('dragpan scrollzoom pinchzoom dragfree', () => { manualCamera.current = true })
    let frame = 0, width = container.current.clientWidth, height = container.current.clientHeight
    const observer = new ResizeObserver(entries => {
      const bounds = entries[0]?.contentRect
      if (!bounds || (Math.abs(bounds.width - width) < 1 && Math.abs(bounds.height - height) < 1)) return
      width = bounds.width; height = bounds.height
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        instance.resize()
        if (!manualCamera.current) arrangeGraph(instance)
      })
    })
    observer.observe(container.current)
    cy.current = instance
    return () => { observer.disconnect(); cancelAnimationFrame(frame); instance.destroy(); cy.current = null }
  }, [table])
  useEffect(() => {
    const instance = cy.current
    if (!instance) return
    const prior = new Map(instance.nodes().map((node) => [node.id(), node.position()]))
    const zoom = instance.zoom(), pan = instance.pan()
    instance.batch(() => {
      instance.elements().remove()
      instance.add(graph.nodes.map((n, index) => ({ data: { id: n.service_id, label: n.display_name }, position: prior.get(n.service_id) ?? { x: 100 + (index % 5) * 140, y: 80 + Math.floor(index / 5) * 110 } })))
      instance.add(graph.edges.map((edge) => ({ data: { id: edge.edge_id, source: edge.source_service_id, target: edge.target_service_id, evidence: edge.supporting_evidence_ids[0], snapshot: graph.snapshot_id, cutoff: graph.as_of, temporal: graph.temporal_mode } })))
      const selectedPaths = paths.filter((path) => selected && path.service_ids.includes(selected))
      const support = new Set(selectedPaths.flatMap((path) => path.service_ids))
      const supportedEdges = new Set(selectedPaths.flatMap((path) => path.edges.map((edge) => `${edge.source_service_id}:${edge.target_service_id}`)))
      if (selected) support.add(selected)
      instance.nodes().forEach((node) => { if (support.has(node.id())) node.addClass('support') })
      instance.edges().forEach((edge) => { if (supportedEdges.has(`${edge.source().id()}:${edge.target().id()}`)) edge.addClass('support') })
    })
    if (!prior.size) arrangeGraph(instance)
    else { instance.zoom(zoom); instance.pan(pan) }
  }, [graph, paths, selected, table])
  return <section className="panel"><div className="panel-heading"><h2>Observed dependencies</h2><div className="toolbar"><button aria-pressed={!table} onClick={() => setTable(false)}>Graph</button><button aria-pressed={table} onClick={() => setTable(true)}>Accessible table</button>{!table && <button onClick={() => { manualCamera.current = false; if (cy.current) arrangeGraph(cy.current) }}>Reset view</button>}</div></div><GraphLegend />
    {graph.truncated && <p className="notice">Display truncated at 80 services / 120 edges. Evidence outside this view is not shown.</p>}
    {table ? <GraphTable graph={graph} onOpen={onOpen} onSelect={setInspected} /> : <div ref={container} className="graph" role="img" aria-label={`Observed dependency graph with ${graph.nodes.length} services. Use Accessible table for keyboard evidence access.`} />}
    {node && <aside aria-label="Selected service" className="notice"><h3>{node.display_name}</h3><p className="mono">{node.service_id}</p>{point ? <><p>Latest eligible ten-second SERVER bucket: {point.sample_count} requests, {point.error_count} errors, p95 {point.p95_ms ?? 'missing'} ms.</p><button onClick={() => onOpen({id:'observation:' + point.observation_id,snapshot:graph.snapshot_id,cutoff:graph.as_of,temporal:graph.temporal_mode,from:node.display_name + ' latest bucket'})}>Inspect bucket source</button></> : <p>No bucket metrics loaded for this service at this cutoff.</p>}<p>{node.quality_flags.join(', ')}</p></aside>}
    <small>As of {graph.as_of} · {graph.nodes.length} services · {graph.edges.length} observed calls</small>
  </section>
}
