import { Link } from 'react-router-dom'
import type { Models } from '../../lib/api/client'
import { LimitationsPanel } from '../../components/ui/common'
import { EvidenceCitation, type Citation } from '../evidence/EvidenceDrawer'
export function ResultPanel({ result, names = new Map(), onOpen, selected, onSelect, stale = false }: { result: Models['InvestigationResult']; names?: Map<string, string>; onOpen: (c: Citation) => void; selected?: string | null; onSelect?: (id: string) => void; stale?: boolean }) {
  const citation = (id: string, from: string): Citation => ({ id, from, snapshot: result.snapshot_id, cutoff: result.as_of, temporal: result.temporal_mode })
  return <section className="result" id="investigation-findings"><div className="panel-heading"><h2>Investigation findings</h2><span className="badge">{result.generation_origin.replaceAll('_', ' ')}</span></div>
    {stale && <p role="status" className="notice">Previous explanation: its cutoff or settings differ from this view. Run explicitly to investigate the new selection. Citations still open the original result scope.</p>}
    {!result.provenance.template_version && <p className="notice">Historical report: this result does not record a grounding policy version. Earlier reports may include services without abnormal measurements or omit baseline context. Review its sources before relying on its hypotheses.</p>}
    {!['tracecause-template-v7', 'tracecause-template-v8'].includes(result.provenance.template_version ?? '') && <p className="notice">Historical citation offsets: this saved report predates the normalized-file offset repair. Its original bytes are preserved. Open a citation to resolve the current source location by its scoped evidence ID.</p>}
    {result.fallback_reason && <p className="notice">Provider fallback: {result.fallback_reason}. This explanation uses the deterministic template.</p>}
    {result.warnings.includes('llm_selected_facts_server_rendered') && <p className="notice">AI selected the facts for this summary. Values and citations come from the recorded evidence.</p>}
    <p className="summary">{result.narrative.summary.text}</p><p className="badge">{result.narrative.status.replaceAll('_', ' ')}</p>
    {result.narrative.summary.evidence_ids.map((id, index) => <EvidenceCitation key={id} citation={citation(id, 'Summary')} onOpen={onOpen} label={`Summary source ${index + 1}`} />)}
    <div className="hypotheses">{result.narrative.hypotheses.map((hypothesis) => <article className={`panel hypothesis ${selected === hypothesis.service_id ? 'selected' : ''}`} key={hypothesis.service_id}>
      <span className="eyebrow">#{hypothesis.rank} · Hypothesis</span><h3><button className="text-button" onClick={() => onSelect?.(hypothesis.service_id)} title={hypothesis.service_id}>{names.get(hypothesis.service_id) ?? hypothesis.service_id.slice(0,16)}</button></h3>
      {result.ranked_scores.filter((s) => s.service_id === hypothesis.service_id).map((s) => <p key={s.service_id}>{s.score_type.replaceAll('_', ' ')}: {s.score.toFixed(3)} <small>(not a probability)</small></p>)}
      <p>{hypothesis.supporting_evidence_ids.length} supporting sources</p>
      {hypothesis.claim_ids.map((id) => result.narrative.claims.find((claim) => claim.claim_id === id)).filter((claim) => !!claim).map((claim) => <div key={claim.claim_id}><p>{claim.text}</p>{claim.evidence_ids.map((id, index) => <EvidenceCitation key={id} citation={citation(id, claim.text)} onOpen={onOpen} label={`Claim source ${index + 1}`} />)}</div>)}
      {!!hypothesis.conflicting_evidence_ids.length && <><h4>Conflicting evidence</h4>{hypothesis.conflicting_evidence_ids.map((id) => <EvidenceCitation key={id} citation={citation(id, 'Conflicting evidence')} onOpen={onOpen} />)}</>}
      {!!hypothesis.diagnostic_check_ids.length && <div><p>Read-only checks from authored sources:</p>{hypothesis.diagnostic_check_ids.map((id,index) => <EvidenceCitation key={id} citation={citation(id,'Read-only diagnostic suggestion')} onOpen={onOpen} label={`Read-only check ${index+1}`} />)}</div>}
    </article>)}</div>
    <details><summary>All structured claims ({result.narrative.claims.length})</summary>{result.narrative.claims.map((claim) => <div className="claim" key={claim.claim_id}><strong>{claim.claim_type}</strong><p>{claim.text}</p>{claim.evidence_ids.map((id, index) => <EvidenceCitation key={id} citation={citation(id, claim.text)} onOpen={onOpen} label={`Source ${index+1}`} />)}</div>)}</details>
    {!!result.paths.length && <details open><summary>Validated support paths</summary>{result.paths.map((path) => <div className="path" key={path.path_id}><button onClick={() => onSelect?.(path.service_ids[0])}>{path.service_ids.map((id) => names.get(id) ?? id.slice(0,8)).join(' → ')}</button>{path.edges.map((edge) => edge.supporting_evidence_ids.map((id) => <EvidenceCitation key={edge.edge_id + id} citation={citation(id, 'Validated path')} onOpen={onOpen} />))}</div>)}</details>}
    <LimitationsPanel items={[...result.narrative.limitations, ...result.warnings.filter(w => w !== 'llm_selected_facts_server_rendered')]} />
    <details><summary>Reproducibility and saved result</summary><dl>{Object.entries(result.provenance).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd className="mono">{value ?? 'Not applicable'}</dd></div>)}</dl><p>Original cutoff: {result.as_of} · {result.temporal_mode}</p><Link to={'/investigations/' + result.investigation_id}>Saved result permalink</Link></details>
  </section>
}
