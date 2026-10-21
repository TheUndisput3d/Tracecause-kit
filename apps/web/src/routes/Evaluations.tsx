import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, query } from '../lib/api/client'
import { ErrorPanel, Loading, OriginBadge } from '../components/ui/common'
const categories: Record<string, RegExp> = {
  Retrieval: /recall|ndcg|reciprocal|retrieval|context|precision/,
  'Fault localization': /hit_at|mrr|ranking|candidate/,
  'Citation audit': /citation|claim|support|numeric|path/,
  Abstention: /coverage|abstain|answer|healthy/,
  Performance: /latency|duration|memory|elapsed|time|rss/,
  Ablations: /ablation/,
}
export function Evaluations() {
  const { reportId } = useParams()
  const [cursor, setCursor] = useState<string | null>(null), [tab, setTab] = useState('Fault localization')
  const list = useQuery({ queryKey: ['evaluations', cursor], enabled: !reportId, queryFn: ({ signal }) => api('EvaluationPage', '/evaluations' + query({ cursor }), { signal }) })
  const detail = useQuery({ queryKey: ['evaluation', reportId], enabled: !!reportId, queryFn: ({ signal }) => api('EvaluationReport', '/evaluations/' + reportId, { signal }) })
  const current = reportId ? detail : list
  const report = detail.data
  const metrics = report ? Object.entries(report.metrics).filter(([key]) => tab === 'All metrics' || categories[tab]?.test(key)) : []
  return <><p className="eyebrow">Measured results</p><h1>{reportId ? 'Evaluation report' : 'Published evaluations'}</h1><p>Separate synthetic and captured demo results. Missing values are undefined or not measured; they are never replaced by illustrative percentages.</p>
    <div className="toolbar"><Link to="/about/data">Synthetic data card</Link><Link to="/about/capture">Captured data card</Link><Link to="/about/model">Model card</Link></div>
    {current.isPending && <Loading />}{current.error && <ErrorPanel error={current.error} retry={() => void current.refetch()} />}
    {!reportId && list.data && <section className="panel">{!list.data.items.length ? <p className="notice">NOT RUN / no published evaluation artifact. An operator can register a sanitized local report.</p> : <table><thead><tr><th>Report</th><th>Origin</th><th>Episodes</th><th>Published</th></tr></thead><tbody>{list.data.items.map((item) => <tr key={item.report_id}><td><Link to={'/evaluations/' + item.report_id}>{item.report_id.slice(0,8)}</Link></td><td><OriginBadge origin={item.origin} /></td><td>{item.sample_size}</td><td>{item.published_at}</td></tr>)}</tbody></table>}<button disabled={!list.data.next_cursor} onClick={() => setCursor(list.data!.next_cursor)}>Next reports</button></section>}
    {report && <><div className="toolbar"><OriginBadge origin={report.origin} /><span className="badge">{report.sample_size} episodes</span><span className="badge">AI review · No independent human validation</span></div><p className="notice">Controlled demonstration data. Small subgroup denominators limit interpretation. Captured learned ranking did not establish superiority; heuristic remains the default. Citation presence does not establish semantic support.</p>
      <div className="toolbar tabs" role="group" aria-label="Report sections">{[...Object.keys(categories),'All metrics','Limitations'].map((name) => <button key={name} aria-pressed={tab === name} onClick={() => setTab(name)}>{name}</button>)}</div>
      {tab === 'Limitations' ? <section className="panel"><h2>Interpretation limits</h2><p>Sample size is the report total. Read per-split denominator metrics alongside scores. Null is undefined, not zero. Bootstrap bounds apply only where explicitly recorded. Test cases without identifiable causes do not have a unique-target ranking score.</p><p>Commit, feature schema and hardware are not included in this sanitized aggregate contract; consult the local evaluation report and dataset/model cards. No private labels are served.</p></section> : <section className="panel"><h2>{tab}</h2>{metrics.length ? <div className="table-scroll"><table><thead><tr><th>Split / system / metric</th><th>Measured value</th></tr></thead><tbody>{metrics.map(([key,value]) => <tr key={key}><td className="mono">{key}</td><td>{value == null ? 'Undefined / not measured' : Number.isInteger(value) ? value : value.toPrecision(5)}</td></tr>)}</tbody></table></div> : <p>NOT RUN / not included in this published artifact.</p>}</section>}
      <details><summary>Dataset and model identity</summary><dl>{(['dataset_hash','split_hash','config_hash','model_digest'] as const).map((key) => <div key={key}><dt>{key}</dt><dd className="mono">{report[key] ?? 'Not applicable'}</dd></div>)}</dl></details>
    </>}
  </>
}
