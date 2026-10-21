import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowDownToLine, ArrowRight, Check, ChevronRight, Layers3, Search, ShieldCheck } from 'lucide-react'
import { api, query } from '../lib/api/client'
import { ErrorPanel, Loading, OriginBadge, SnapshotBadge, utc } from '../components/ui/common'

export function Overview() {
  const [cursor, setCursor] = useState<string | null>(null), [origin, setOrigin] = useState('')
  const [search, setSearch] = useState(''), [status, setStatus] = useState('')
  const response = useQuery({ queryKey: ['episodes', cursor, origin], queryFn: ({ signal }) => api('EpisodePage', '/episodes' + query({ cursor, origin: origin || null, limit: 20 }), { signal }) })
  const items = response.data?.items ?? []
  const rows = items.filter((row) => row.display_name.toLowerCase().includes(search.toLowerCase()) && (!status || (status === 'READY' ? !!row.active_snapshot_id : row.ingestion_status === status))).sort((a, b) => Number(b.origin === 'captured_demo') - Number(a.origin === 'captured_demo') || (a.origin === 'captured_demo' ? Date.parse(b.event_interval.end) - Date.parse(a.event_interval.end) : 0))
  const demo = rows.find(row => row.active_snapshot_id)
  return <>
    <div className="page-heading"><div><p className="eyebrow">Investigation workspace</p><h1>Incident library</h1><p className="lede">Explore recorded applications, replay their incidents, and connect service behavior to the changes and evidence around it.</p></div><Link className="button" to="/admin/imports"><ArrowDownToLine size={16} aria-hidden="true" />Import an episode</Link></div>
    <div className="overview-intro"><div className="intro-icon"><Layers3 size={21} aria-hidden="true" /></div><div><strong>Your investigation desk</strong><p>Choose a recorded episode to replay its timeline and explore the connected evidence.</p></div>{demo && <Link className="button primary" to={'/episodes/' + demo.episode_id}>Try demo<ArrowRight size={16} aria-hidden="true" /></Link>}</div>
    {response.data && <dl className="metrics-strip" aria-label="Loaded page summary"><div><dt>Episodes on this page</dt><dd>{items.length.toString().padStart(2, '0')}</dd></div><div><dt>Published snapshots</dt><dd>{items.filter(row => row.active_snapshot_id).length.toString().padStart(2, '0')}</dd></div><div><dt>Captured episodes</dt><dd>{items.filter(row => row.origin === 'captured_demo').length.toString().padStart(2, '0')}</dd></div><div className="metric-note"><ShieldCheck size={23} aria-hidden="true" /><p>Scoped to the snapshot.<br /><strong>Grounded in its sources.</strong></p></div></dl>}
    <section className="panel episode-ledger">
      <div className="panel-heading"><div><p className="section-kicker">Evidence library</p><h2>Recorded episodes</h2></div><span className="record-count">{response.data ? `${rows.length} on this page` : 'Loading library'}</span></div>
      <div className="toolbar filters"><label className="search-field"><span>Search this page</span><span className="input-with-icon"><Search size={16} aria-hidden="true" /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find an episode…" /></span></label>
        <label>Origin<select value={origin} onChange={(e) => { setOrigin(e.target.value); setCursor(null) }}><option value="">All origins</option><option value="synthetic">Synthetic</option><option value="captured_demo">Captured demo</option></select></label>
        <label>Status on this page<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option><option>READY</option><option>QUEUED</option><option>FAILED</option></select></label>
      </div>
      {response.isPending && <Loading />}{response.error && <ErrorPanel error={response.error} retry={() => void response.refetch()} />}
      {response.data && <>
        {!rows.length ? <div className="empty"><Search size={23} aria-hidden="true" /><h3>No matching episodes</h3><p>Adjust the filters or add a manifest and its source files through <Link to="/admin/imports">Local imports</Link>.</p></div> : <div className="table-scroll"><table className="episode-table"><thead><tr><th>Episode / origin</th><th>Recorded interval · UTC</th><th>Evidence</th><th>Snapshot</th><th>Latest investigation</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.episode_id}>
          <td><div className="episode-title"><span className="row-number">{String(index + 1).padStart(2, '0')}</span><div><Link to={'/episodes/' + row.episode_id}>{row.display_name}<ChevronRight size={14} aria-hidden="true" /></Link><OriginBadge origin={row.origin} /></div></div></td>
          <td className="interval-cell"><time dateTime={row.event_interval.start}>{utc(row.event_interval.start)}</time><span>to {utc(row.event_interval.end)}</span></td>
          <td><strong>{row.service_count}</strong> services<span className="cell-secondary">{row.valid_span_count.toLocaleString()} valid spans</span></td>
          <td><span className={'status-label ' + (row.active_snapshot_id ? 'status-ready' : '')}>{row.active_snapshot_id && <Check size={12} aria-hidden="true" />}{row.active_snapshot_id ? 'Ready' : row.ingestion_status}</span><span className="cell-secondary"><SnapshotBadge id={row.active_snapshot_id} /></span></td>
          <td><span className="cell-status">{row.last_investigation_status?.replaceAll('_', ' ').toLowerCase() ?? 'Not started'}</span><span className="cell-secondary">{row.availability_recorded ? 'Availability recorded' : 'Event-time replay'}</span>{row.quality_flags.length > 0 && <details><summary>Quality notes</summary>{row.quality_flags.join(', ')}</details>}</td>
        </tr>)}</tbody></table></div>}
        <div className="ledger-footer"><small>Showing {rows.length} of {items.length} loaded episodes. Up to 20 per page.</small><div className="toolbar"><button disabled={!cursor} onClick={() => setCursor(null)}>First page</button><button disabled={!response.data.next_cursor} onClick={() => setCursor(response.data!.next_cursor)}>Next page<ArrowRight size={14} aria-hidden="true" /></button></div></div>
      </>}
    </section>
    <aside className="provenance-note"><ShieldCheck size={20} aria-hidden="true" /><div><strong>Know what you’re looking at.</strong><p>Synthetic fixtures model scenarios. Captured demo traces come from controlled applications. Both are labeled throughout the workspace; neither establishes production accuracy. Evaluation labels have AI review, without independent human validation.</p></div><Link to="/about/data">Read the data card<ArrowRight size={14} aria-hidden="true" /></Link></aside>
  </>
}
