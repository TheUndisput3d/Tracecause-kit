import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Clock3, GitBranch, Play, ShieldCheck } from 'lucide-react'
import { useApp } from '../app/context'
import { api, post, query, validate, type Models } from '../lib/api/client'
import schemas from '../lib/api/schemas.json'
import { queryKeys } from '../lib/queryKeys'
import { Empty, ErrorPanel, Loading, OriginBadge, SnapshotBadge, utc } from '../components/ui/common'
import { DependencyGraph } from '../features/graph/DependencyGraph'
import { SignalTimeline } from '../features/graph/SignalTimeline'
import { KnowledgeExplorer } from '../features/graph/KnowledgeExplorer'
import { GraphComparison } from '../features/graph/GraphComparison'
import { EvidenceDrawer, type Citation } from '../features/evidence/EvidenceDrawer'
import { ResultPanel } from '../features/investigations/ResultPanel'
import { InvestigationProgress, terminal, useJob } from '../features/investigations/InvestigationProgress'
import { OperationsPanel, type OperationsView } from '../features/operations/OperationsPanel'

const methods = schemas.InvestigationRequest.$defs.RetrievalMode.enum
const temporals = schemas.InvestigationRequest.$defs.TemporalMode.enum
export function Workbench() {
  const { episodeId = '' } = useParams()
  return <EpisodeWorkbench key={episodeId} episodeId={episodeId} />
}
function EpisodeWorkbench({ episodeId }: { episodeId: string }) {
  const { token, config } = useApp()
  const cache = useQueryClient()
  const [pinnedGraph, setPinnedGraph] = useState<Models['GraphResponse'] | null>(null)
  const [operationsView, setOperationsView] = useState<OperationsView>('changes'), [contextService, setContextService] = useState('')
  const [savedCursor,setSavedCursor] = useState<string | null>(null)
  const [params, setParams] = useSearchParams()
  const episode = useQuery({ queryKey: queryKeys.episode(episodeId), queryFn: ({ signal }) => api('EpisodeView', '/episodes/' + episodeId, { signal }) })
  const [question, setQuestion] = useState('Which services should be investigated, and what evidence supports them?')
  const [symptomChoice, setSymptom] = useState<string | null>(null), [ranking, setRanking] = useState<Models['RankingMode']>('heuristic')
  const symptom = symptomChoice ?? params.get('symptom') ?? episode.data?.default_symptom_service_id ?? ''
  const [generation, setGeneration] = useState<Models['GenerationMode']>('template')
  const [citation, setCitation] = useState<Citation | null>(null), [selected, setSelected] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState<Models['InvestigationSubmission'] | null>(null)
  const [submittedRequest, setSubmittedRequest] = useState<Models['InvestigationRequest'] | null>(null)
  const snapshot = params.get('snapshot') ?? episode.data?.active_snapshot_id ?? ''
  const rawCutoff = params.get('as_of') ?? episode.data?.default_as_of ?? episode.data?.event_interval.end ?? ''
  const cutoff = /(Z|[+-]\d{2}:\d{2})$/.test(rawCutoff) && Number.isFinite(Date.parse(rawCutoff)) ? new Date(rawCutoff).toISOString() : rawCutoff
  const method = params.get('method') ?? 'GRAPH', temporal = params.get('temporal') ?? 'event_time_replay'
  const valid = !!episode.data && !!snapshot && /(Z|[+-]\d{2}:\d{2})$/.test(cutoff) && Number.isFinite(Date.parse(cutoff)) && Date.parse(cutoff) >= Date.parse(episode.data.event_interval.start) && Date.parse(cutoff) <= Date.parse(episode.data.event_interval.end) && methods.includes(method) && temporals.includes(temporal)
  useEffect(() => {
    if (!episode.data) return
    if (['snapshot','as_of','method','temporal'].every((key) => params.has(key)) && params.get('as_of') === cutoff) return
    const next = new URLSearchParams(params)
    Object.entries({snapshot,as_of:cutoff,method,temporal}).forEach(([key,value]) => next.set(key,value))
    setParams(next,{replace:true})
  },[episode.data,params,setParams,snapshot,cutoff,method,temporal])
  const identity = JSON.stringify([episodeId, snapshot, cutoff, temporal, method])
  const [settled, setSettled] = useState(identity)
  useEffect(() => { const timeout = setTimeout(() => setSettled(identity), 300); return () => clearTimeout(timeout) }, [identity])
  const ready = valid && identity === settled
  const scope = { episode_id: episodeId, snapshot_id: snapshot, as_of: cutoff, temporal_mode: temporal }
  const graph = useQuery({ queryKey: queryKeys.scoped('graph', episodeId, snapshot, cutoff, temporal, method, config?.config_hash), enabled: ready,
    queryFn: ({ signal }) => api('GraphResponse', '/graph' + query(scope), { signal }) })
  const series = useQuery({ queryKey: queryKeys.scoped('series', episodeId, snapshot, cutoff, temporal, method, config?.config_hash), enabled: ready,
    queryFn: ({ signal }) => api('SeriesResponse', '/episodes/' + episodeId + '/series' + query(scope), { signal }) })
  const saved = useQuery({ queryKey: ['saved', episodeId, savedCursor], queryFn: ({ signal }) => api('InvestigationPage', '/episodes/' + episodeId + '/investigations' + query({cursor:savedCursor}), { signal }) })
  const names = new Map(graph.data?.nodes.map((node) => [node.service_id, node.display_name]))
  const run = useMutation({ mutationFn: async () => {
    const request = validate('InvestigationRequest', { ...scope, question, symptom_service_id: symptom || null, retrieval_mode: method, ranking_mode: ranking, generation_mode: generation })
    const next = await post('InvestigationSubmission', '/investigations', token, request)
    setSubmittedRequest(request)
    return next
  }, onSuccess: setSubmitted })
  const job = useJob(submitted?.job_id ?? null)
  useEffect(() => {
    if (job.data?.status === 'SUCCEEDED') {
      void cache.invalidateQueries({queryKey:['saved',episodeId]})
      void cache.invalidateQueries({queryKey:queryKeys.episode(episodeId)})
    }
  },[cache,episodeId,job.data?.status,submitted?.job_id])
  const result = useQuery({ queryKey: ['result', submitted?.investigation_id], enabled: job.data?.status === 'SUCCEEDED',
    queryFn: ({ signal }) => api('InvestigationView', '/investigations/' + submitted!.investigation_id, { signal }) })
  const update = (values: Record<string,string>) => { setParams((previous) => { const next = new URLSearchParams(previous); Object.entries({ snapshot, as_of: cutoff, method, temporal, ...values }).forEach(([key,value]) => next.set(key,value)); return next }, { replace: true }) }
  if (episode.isPending) return <Loading />
  if (episode.error) return <ErrorPanel error={episode.error} retry={() => void episode.refetch()} />
  if (!episode.data) return <Empty text="Episode not found." />
  const data = episode.data, start = Date.parse(data.event_interval.start), end = Date.parse(data.event_interval.end)
  const busy = run.isPending || (!!submitted && !terminal(job.data?.status))
  const stale = submittedRequest && (Date.parse(submittedRequest.as_of) !== Date.parse(cutoff) || submittedRequest.snapshot_id !== snapshot || submittedRequest.temporal_mode !== temporal || submittedRequest.retrieval_mode !== method || submittedRequest.question !== question || submittedRequest.ranking_mode !== ranking || submittedRequest.generation_mode !== generation || submittedRequest.symptom_service_id !== (symptom || null))
  return <><Link to="/" className="back-link"><ArrowLeft size={14} aria-hidden="true" />All episodes</Link>
    <div className="panel-heading episode-heading"><div><p className="eyebrow">Episode investigation</p><h1>{data.display_name}</h1></div><OriginBadge origin={data.origin} /></div>
    <div className="metadata"><SnapshotBadge id={snapshot || null} /><span>{valid ? utc(cutoff) : 'Invalid cutoff'}</span><span>{temporal.replaceAll('_',' ')}</span></div>
    <div className="episode-scope-strip"><div><GitBranch size={17} aria-hidden="true" /><strong>{data.service_count}</strong><span>services</span></div><div><strong>{data.valid_span_count.toLocaleString()}</strong><span>valid spans</span></div><div><Clock3 size={16} aria-hidden="true" /><span>{((end - start) / 60000).toFixed(1)} minute recording</span></div><div><ShieldCheck size={16} aria-hidden="true" /><span>{data.availability_recorded ? 'Availability recorded' : 'Event-time replay'}</span></div></div>
    <nav className="section-nav" aria-label="Episode sections"><a href="#investigate">Investigation</a><a href="#operational-context">Changes & history</a><a href="#recorded-signals">Signals & dependencies</a><a href="#connected-knowledge">Connected evidence</a><a href="#saved-investigations">Saved results<ArrowRight size={13} aria-hidden="true" /></a></nav>
    <details aria-label="Data quality" className="data-quality"><summary>About this recording and its limits</summary><p>{data.availability_recorded ? 'Availability timestamps recorded.' : 'Availability timestamps are missing. Event-time replay does not establish when data became available.'} Whole episode: {data.valid_span_count} valid spans across {data.service_count} services. {data.quality_flags.join(" · ")} Recorded spans can be incomplete; hypotheses are not confirmed causes.</p></details>
    {!snapshot && <Empty text="No published snapshot. Finish or retry the import before investigating." />}
    <section className="panel investigation-composer compact-composer" id="investigate"><div className="panel-heading"><h2>Replay and question</h2><span className="badge">Snapshot-bound evidence</span></div>
      <div className="compact-replay"><div className="replay-control"><label>Cutoff in ten-second steps<input aria-label="Replay cutoff" type="range" min={start} max={end} step={10000} value={valid ? Date.parse(cutoff) : end} onChange={(e) => update({ as_of: new Date(Number(e.target.value)).toISOString() })} disabled={!snapshot} /></label><div className="replay-endpoints"><span>{utc(data.event_interval.start)}</span><span>{utc(data.event_interval.end)}</span></div></div><label>Exact UTC cutoff<input aria-label="Exact UTC cutoff" type="text" value={cutoff} onChange={(e) => update({ as_of: e.target.value })} /></label></div>
      {!valid && snapshot && <p role="alert">Use a valid UTC cutoff within the episode and a supported replay/retrieval mode.</p>}
      <form onSubmit={(e) => { e.preventDefault(); run.mutate() }}><div className="question-row"><label>Investigation question<textarea rows={2} maxLength={config?.maximum_question_characters ?? 2000} required value={question} onChange={(e) => setQuestion(e.target.value)} /></label><button className="primary" disabled={!valid || !ready || !question.trim() || !token || !config?.mutations_enabled || busy} type="submit"><Play size={14} aria-hidden="true" />{busy ? 'Investigation running…' : 'Run investigation'}</button></div></form>
      <details className="advanced-controls"><summary><span>Investigation settings</span><span className="settings-summary">{method} · {ranking} · {generation}</span></summary><div className="controls"><label>Replay mode<select aria-label="Replay mode" value={temporal} onChange={(e) => update({ temporal: e.target.value })}>{temporals.map((value) => <option disabled={value === 'available_time_replay' && !data.availability_recorded} key={value} value={value}>{value.replaceAll('_',' ')}</option>)}</select></label>
        <label>Retrieval method<select aria-label="Retrieval method" value={method} onChange={(e) => update({ method: e.target.value })}>{methods.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Ranking<select aria-label="Ranking" value={ranking} onChange={(e) => setRanking(e.target.value as Models['RankingMode'])}><option value="heuristic">Heuristic</option>{config?.ranking_modes.includes('learned') && <option value="learned">Learned (experimental)</option>}</select></label>
        <label>Generation<select aria-label="Generation" value={generation} onChange={(e) => setGeneration(e.target.value as Models['GenerationMode'])}>{(config?.generation_modes ?? ['template']).map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Symptom service<select aria-label="Symptom service" value={symptom} onChange={(e) => setSymptom(e.target.value)}><option value="">No service specified</option>{graph.data?.nodes.map((node) => <option key={node.service_id} value={node.service_id}>{node.display_name}</option>)}</select></label></div><small>{question.length}/2000 characters. Replay changes never trigger model execution.</small></details>
      {!token && config?.mutations_enabled && <p className="operator-hint">Connect your local operator token to run an investigation. Recorded evidence is available without connecting.</p>}
    </section>
    {run.error && <ErrorPanel error={run.error} />}{job.error && <ErrorPanel error={job.error} retry={() => void job.refetch()} />}{job.data && <InvestigationProgress job={job.data} refresh={() => void job.refetch()} />}
    {!ready && valid && <Loading text="Updating replay scope…" />}{graph.error && <ErrorPanel error={graph.error} retry={() => void graph.refetch()} />}{series.error && <ErrorPanel error={series.error} retry={() => void series.refetch()} />}
    {ready && graph.data && <OperationsPanel graph={graph.data} symptom={symptom} onOpen={setCitation} tab={operationsView} setTab={setOperationsView} focus={contextService} setFocus={setContextService} />}
    <div id="recorded-signals" className="section-anchor" />{ready && series.data && <SignalTimeline series={series.data} names={names} baseline={data.baseline_interval} />}
    <div className={result.data?.result ? "investigation-grid" : ""}>
    {result.error && <ErrorPanel error={result.error} />}{result.data?.result && <ResultPanel result={result.data.result} names={names} onOpen={setCitation} selected={selected} onSelect={setSelected} stale={!!stale} />}
    {ready && graph.data && <DependencyGraph series={series.data} graph={graph.data} selected={stale ? null : selected} paths={!stale ? result.data?.result?.paths ?? [] : []} onOpen={setCitation} />}
    </div>
    <div id="connected-knowledge" className="section-anchor" />{ready && graph.data && <><GraphComparison before={pinnedGraph} setBefore={setPinnedGraph} graph={graph.data} onOpen={setCitation} /><KnowledgeExplorer graph={graph.data} onOpen={setCitation} /></>}
    <section className="panel" id="saved-investigations"><div className="panel-heading"><div><p className="section-kicker">Investigation archive</p><h2>Saved investigations</h2></div><span className="badge">Immutable results</span></div>{saved.data?.items.length ? <ul className="saved-list">{saved.data.items.map((item) => <li key={item.investigation_id}><Link to={'/investigations/' + item.investigation_id}><Clock3 size={15} aria-hidden="true" />{utc(item.created_at)}<span>{item.status}</span><ArrowRight size={14} aria-hidden="true" /></Link></li>)}</ul> : <p className="muted">No saved investigations on this page. Completed investigations will appear here.</p>}{saved.error && <ErrorPanel error={saved.error} />}<div className="toolbar"><button disabled={!savedCursor} onClick={() => setSavedCursor(null)}>First saved page</button><button disabled={!saved.data?.next_cursor} onClick={() => setSavedCursor(saved.data!.next_cursor)}>Next saved page</button></div></section>
    <EvidenceDrawer citation={citation} close={() => setCitation(null)} /></>
}
