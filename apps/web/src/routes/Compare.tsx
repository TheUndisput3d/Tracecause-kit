import { useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, post, type Models } from '../lib/api/client'
import { useApp } from '../app/context'
import { terminal } from '../features/investigations/InvestigationProgress'
import { ErrorPanel } from '../components/ui/common'
export function Compare() {
  const { token, config } = useApp()
  const [episodeId, setEpisodeId] = useState(''), [question, setQuestion] = useState('Which services should be investigated, and what evidence supports them?')
  const [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null), [jobIds, setJobIds] = useState<string[]>([])
  const [controlled, setControlled] = useState(false)
  const episodes = useQuery({ queryKey:['comparison-episodes'], queryFn:({ signal }) => api('EpisodePage','/episodes',{ signal }) })
  const episode = episodes.data?.items.find((item) => item.episode_id === episodeId)
  const jobs = useQueries({ queries: jobIds.map((id) => ({ queryKey:['job',id], queryFn:({ signal }: { signal:AbortSignal }) => api('JobView','/jobs/' + id,{ signal }), refetchInterval: (query: { state: { data?: Models['JobView'] } }) => terminal(query.state.data?.status) ? false as const : 1000 })) })
  const runComparison = async () => {
    if (!episode?.active_snapshot_id) return
    setBusy(true); setError(null); setControlled(true)
    const submissions = await Promise.allSettled((['LEXICAL','DENSE','HYBRID','GRAPH'] as const).map((method) => post('InvestigationSubmission','/investigations',token,{ episode_id:episode.episode_id,snapshot_id:episode.active_snapshot_id,as_of:episode.event_interval.end,temporal_mode:'event_time_replay',question,symptom_service_id:null,retrieval_mode:method,ranking_mode:'heuristic',generation_mode:'template' })))
    const accepted = submissions.flatMap((item) => item.status === 'fulfilled' ? [item.value] : [])
    setIds(accepted.map((item) => item.investigation_id)); setJobIds(accepted.map((item) => item.job_id))
    const failed = submissions.find((item) => item.status === 'rejected')
    if (failed?.status === 'rejected') setError(failed.reason)
    setBusy(false)
  }
  const [first, setFirst] = useState(''), [second, setSecond] = useState(''), [ids, setIds] = useState<string[]>([])
  const results = useQueries({ queries: ids.map((id,index) => ({ queryKey:['saved-result',id,jobs[index]?.data?.status], queryFn:({ signal }: { signal:AbortSignal }) => api('InvestigationView','/investigations/' + encodeURIComponent(id),{ signal }) })) })
  const reports = useQuery({ queryKey:['evaluations',null], queryFn:({ signal }) => api('EvaluationPage','/evaluations',{ signal }) })
  const saved = results.flatMap((r) => r.data?.result ? [r.data.result] : [])
  const comparable = saved.length >= 2 && saved.every((item) => item.episode_id === saved[0].episode_id && item.snapshot_id === saved[0].snapshot_id && item.as_of === saved[0].as_of && item.temporal_mode === saved[0].temporal_mode)
  const row = (result: Models['InvestigationResult']) => <tr key={result.investigation_id}><td><Link to={'/investigations/' + result.investigation_id}>{result.retrieval_mode}</Link></td><td>{result.generation_origin}</td><td>{result.evidence.length}</td><td>{result.paths.length}</td><td>{Object.entries(result.timings_ms).map(([k,v]) => <div key={k}>{k}: {v.toFixed(1)} ms</div>)}</td><td><details><summary>Evidence IDs</summary>{result.evidence.map((e) => <p className="mono" key={e.evidence_id}>{e.evidence_id}</p>)}</details></td></tr>
  return <><p className="eyebrow">Inspect method differences</p><h1>Compare saved investigations</h1><p>Use immutable result IDs. Local operators can submit a controlled four-method comparison; replay uses saved results. Quality metrics come from published evaluation artifacts.</p>
    {config?.mutations_enabled && <section className="panel"><h2>Run a controlled comparison</h2><p>Four separate template jobs use the same episode, snapshot, question and final cutoff, with the same configured evidence/context limits. This is a local diagnostic, not a new quality benchmark.</p><label>Episode<select aria-label="Episode" value={episodeId} onChange={(e) => setEpisodeId(e.target.value)}><option value="">Select an episode</option>{episodes.data?.items.filter((item) => item.active_snapshot_id).map((item) => <option key={item.episode_id} value={item.episode_id}>{item.display_name}</option>)}</select></label><label>Shared question<textarea value={question} maxLength={2000} onChange={(e) => setQuestion(e.target.value)} /></label><button disabled={!token || !episode || !question.trim() || busy || jobs.some((job) => !terminal(job.data?.status))} onClick={() => void runComparison()}>Run four methods</button>{episode && <p>Shared cutoff: {episode.event_interval.end} · Heuristic ranking · Template generation</p>}{error ? <ErrorPanel error={error} /> : null}{jobs.map((job,index) => <p role="status" key={jobIds[index]}>Job {index+1}: {job.data?.status ?? 'Loading'} · {job.data?.phase}{job.error && ' · Status unavailable; retry by reopening the job.'}</p>)}</section>}
    <form className="panel controls" onSubmit={(e) => { e.preventDefault(); setIds([first.trim(), second.trim()]); setJobIds([]); setControlled(false) }}><label>First result ID<input required value={first} onChange={(e) => setFirst(e.target.value)} /></label><label>Second result ID<input required value={second} onChange={(e) => setSecond(e.target.value)} /></label><button>Load comparison</button></form>
    {results.map((r,i) => r.error ? <ErrorPanel key={i} error={r.error} /> : null)}
    {!!saved.length && <><p className="notice">{comparable ? controlled ? 'This run used the same question, scope and configured context budget for every submitted method.' : 'Episode, snapshot, cutoff and temporal mode match. Confirm the original question and configured context budget before interpreting quality differences.' : 'Scopes differ or a result is unavailable. These results are not a controlled method comparison.'}</p><div className="table-scroll panel"><table><thead><tr><th>Method</th><th>Generation</th><th>Evidence count</th><th>Paths</th><th>Measured timings</th><th>Sources</th></tr></thead><tbody>{saved.map(row)}</tbody></table></div><p>{saved.length === 2 ? `${saved[0].evidence.filter((e) => saved[1].evidence.some((other) => other.evidence_id === e.evidence_id)).length} evidence IDs shared by both results.` : 'Load both results to inspect overlap.'} Candidate recall is NOT MEASURED by this UI.</p></>}
    <section className="panel"><h2>Published quality measurements</h2>{reports.data?.items.length ? <ul>{reports.data.items.map((report) => <li key={report.report_id}><Link to={'/evaluations/' + report.report_id}>{report.origin} · {report.sample_size} episodes</Link></li>)}</ul> : <p>NOT RUN / no published report loaded.</p>}</section>
  </>
}
