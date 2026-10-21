import type { Models } from '../../lib/api/client'
import type { Citation } from '../evidence/EvidenceDrawer'
import { compareGraphs } from './temporal'

export function GraphComparison({ graph, before, setBefore, onOpen }: { before: Models['GraphResponse'] | null; setBefore: (value: Models['GraphResponse'] | null) => void; graph: Models['GraphResponse']; onOpen: (value: Citation) => void }) {
  const comparison = before && compareGraphs(before, graph)
  const names = new Map([...(before?.nodes ?? []), ...graph.nodes].map(node => [node.service_id, node.display_name]))
  return <section className="panel" aria-label="Temporal graph comparison"><h2>What changed in the graph?</h2>
    <p>Pin an earlier cutoff, then move the replay cursor forward to compare observed dependencies in the same snapshot.</p>
    <div className="toolbar"><button onClick={() => setBefore(graph)}>Pin this cutoff</button>{before && <button onClick={() => setBefore(null)}>Clear comparison</button>}</div>
    {before && <p className="mono">Pinned: {before.as_of} · Current: {graph.as_of}</p>}
    {before && !comparison && <p className="notice">Select a later cutoff in the same episode, snapshot and replay mode, or pin a new starting point.</p>}
    {comparison && <><p>{comparison.added.length} newly observed · {comparison.absent.length} no longer observed in this window · {comparison.retained.length} still observed</p>
      <p className="muted">Calls use a six-minute lookback. A missing edge may reflect traffic, sampling or the window moving; it does not prove a dependency was removed.</p>
      {comparison.incomplete && <p className="notice">At least one view was truncated. Counts describe the displayed subset.</p>}
      <div className="table-scroll"><table><caption>Dependency changes and source cutoff</caption><thead><tr><th>Change</th><th>Caller → callee</th><th>Evidence</th></tr></thead><tbody>
        {(['added', 'absent', 'retained'] as const).flatMap(kind => comparison[kind].map(edge => {
          const scope = kind === 'absent' ? before! : graph
          return <tr key={kind + edge.edge_id}><td>{{ added: 'Newly observed', absent: 'Not observed now', retained: 'Still observed' }[kind]}</td><td>{names.get(edge.source_service_id)} → {names.get(edge.target_service_id)}</td><td><button className="citation" onClick={() => onOpen({ id: edge.supporting_evidence_ids[0], snapshot: scope.snapshot_id, cutoff: scope.as_of, temporal: scope.temporal_mode, from: 'Dependency comparison at ' + scope.as_of })}>Inspect {kind === 'absent' ? 'earlier' : 'current'} source</button></td></tr>
        }))}
      </tbody></table></div></>}
  </section>
}
