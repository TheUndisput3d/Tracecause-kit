import { useState, useMemo, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, query, type Models } from '../../lib/api/client'
import { ErrorPanel, Loading } from '../../components/ui/common'
import type { Citation } from '../evidence/EvidenceDrawer'
const KnowledgeCanvas = lazy(() => import('./KnowledgeCanvas'))

export function KnowledgeExplorer({ graph, onOpen }: { graph: Models['GraphResponse']; onOpen: (value: Citation) => void }) {
  const [opened, setOpened] = useState(false), [view, setView] = useState<'graph' | 'table'>('graph')
  const [focus, setFocus] = useState('')
  const scope = { episode_id: graph.episode_id, snapshot_id: graph.snapshot_id, as_of: graph.as_of, temporal_mode: graph.temporal_mode }
  const response = useQuery({ queryKey: ['knowledge', scope], enabled: opened, queryFn: ({ signal }) => api('KnowledgeResponse', '/knowledge' + query(scope), { signal }) })
  const selected = graph.nodes.some(node => node.service_id === focus) ? focus : ''
  const checks = useQuery({ queryKey: ['knowledge-checks', scope, selected], enabled: opened && !!selected,
    queryFn: ({ signal }) => api('KnowledgeChecksResponse', '/knowledge/checks' + query({ ...scope, symptom_service_id: selected }), { signal }) })
  const data = response.data
  const labels = new Map(data?.nodes.map(node => [node.node_id, node.label]))
  const names = new Map(graph.nodes.map(node => [node.service_id, node.display_name]))
  const { nodes, edges } = useMemo(() => {
    const visible = new Set<string>()
    if (selected && data) {
      visible.add('service:' + selected)
      for (let depth = 0; depth < 3; depth++) {
        // Other services may be endpoints, but do not expand their neighborhoods.
        const expandable = (id: string) => visible.has(id) && (!id.startsWith('service:') || id === 'service:' + selected)
        const next = data.edges.filter(edge => edge.relationship !== 'CALLS' && (expandable(edge.source) || expandable(edge.target)))
        next.forEach(edge => { visible.add(edge.source); visible.add(edge.target) })
      }
    }
    return { nodes: data?.nodes.filter(node => !visible.size || visible.has(node.node_id)) ?? [], edges: data?.edges.filter(edge => !visible.size || (visible.has(edge.source) && visible.has(edge.target))) ?? [] }
  }, [data, selected])
  const inspect = (id: string) => onOpen({ id, snapshot: graph.snapshot_id, cutoff: graph.as_of, temporal: graph.temporal_mode, from: 'Knowledge explorer' })
  return <section className="panel" aria-label="Knowledge explorer"><div className="panel-heading"><h2>Knowledge explorer</h2><button aria-expanded={opened} onClick={() => setOpened(!opened)}>{opened ? 'Close knowledge explorer' : 'Explore connected evidence'}</button></div>
    <p>Follow the links between services, measured observations, authored documents, verified assertions and exact excerpts. Every displayed relationship has source evidence.</p>
    {opened && <>
      {response.isPending && <Loading text="Validating connected evidence…" />}{response.error && <ErrorPanel error={response.error} retry={() => void response.refetch()} />}
      {data && <>
        <div className="toolbar"><label>Knowledge focus service<select value={selected} onChange={event => setFocus(event.target.value)}><option value="">All displayed services</option>{graph.nodes.map(node => <option key={node.service_id} value={node.service_id}>{node.display_name}</option>)}</select></label>
          <button aria-pressed={view === 'graph'} onClick={() => setView('graph')}>Evidence graph</button><button aria-pressed={view === 'table'} onClick={() => setView('table')}>Evidence relationships</button></div>
        <p className="muted">Teal: services · Blue: observations · Purple: documents · Gold: excerpts · Pink: authored assertions. MENTIONS and ABOUT_SERVICE link authored statements to services. CALLS records communication; it does not establish causation.</p>
        {data.truncated && <p className="notice">Bounded evidence view: {data.omitted_observations} observations, {data.omitted_chunks} excerpts and {data.omitted_assertions} assertions omitted. Up to 24 recent observations, 12 excerpts, 24 assertions and 256 relationships are displayed.</p>}
        <p>{nodes.length} visible entities · {edges.length} visible relationships</p>
        {view === 'graph' ? <Suspense fallback={<Loading text="Loading evidence graph…" />}><KnowledgeCanvas nodes={nodes} edges={edges} onInspect={inspect} /></Suspense> : <div className="table-scroll"><table><caption>Source-backed knowledge relationships</caption><thead><tr><th>From</th><th>Relationship</th><th>To</th><th>Source</th></tr></thead><tbody>{edges.map((edge, index) => <tr key={index}><td>{labels.get(edge.source)}</td><td>{edge.relationship}</td><td>{labels.get(edge.target)}</td><td><button className="citation" onClick={() => inspect(edge.evidence_ids[0])}>Inspect relationship source</button></td></tr>)}</tbody></table></div>}
        <details><summary>Entities and source excerpts</summary><ul>{nodes.map(node => <li key={node.node_id}><strong>{node.kind}</strong>: {node.label} {node.evidence_ids.length > 0 && <button className="citation" onClick={() => inspect(node.evidence_ids[0])}>Inspect {node.kind} source</button>}</li>)}</ul></details>
      </>}
      {selected && <div className="notice"><h3>Checks connected through dependencies</h3><p>Starting at {names.get(selected)}, follow up to three caller → callee steps, then a service’s verified authored check. Shorter paths appear first. These are source suggestions to review; nothing is executed.</p>
        {checks.isPending && <Loading text="Following verified knowledge paths…" />}{checks.error && <ErrorPanel error={checks.error} retry={() => void checks.refetch()} />}
        {checks.data && !checks.data.checks.length && <p>No eligible, unambiguous authored checks are reachable at this cutoff.</p>}
        {checks.data?.truncated && <p>Results are capped. Some paths or checks may be omitted.</p>}
        {checks.data?.checks.map(check => <article className="claim" key={check.assertion_id}>
          <strong>{check.service_ids.map(id => names.get(id) ?? id.slice(0, 12)).join(' → ')} ← authored check</strong>
          <p className="source-quote">{check.quote}</p><small>{check.document_title} · exact source characters {check.start_char}–{check.end_char}</small>
          <div><button className="citation" onClick={() => inspect(check.evidence_id)}>Inspect check quote</button>{check.call_edges.map((edge, index) => <button className="citation" key={edge.edge_id} onClick={() => inspect(edge.supporting_evidence_ids[0])}>Inspect call step {index + 1}</button>)}</div>
        </article>)}
      </div>}
    </>}
  </section>
}
