import * as Dialog from '@radix-ui/react-dialog'
import { useQuery } from '@tanstack/react-query'
import { FileJson, Fingerprint, X } from 'lucide-react'
import { api, query, type Models } from '../../lib/api/client'
import { ErrorPanel, Loading, utc } from '../../components/ui/common'

export type OperationalCitation = { id: string; episode: string; snapshot: string; cutoff: string; temporal: Models['TemporalMode']; from: string }
export function OperationalEvidenceDrawer({ citation, close }: { citation: OperationalCitation | null; close: () => void }) {
  const evidence = useQuery({ queryKey: ['operational-evidence', citation], enabled: !!citation, queryFn: ({ signal }) => api('OperationalEvidence', '/operations/evidence/' + encodeURIComponent(citation!.id) + query({ episode_id: citation!.episode, snapshot_id: citation!.snapshot, as_of: citation!.cutoff, temporal_mode: citation!.temporal }), { signal }) })
  const data = evidence.data
  return <Dialog.Root open={!!citation} onOpenChange={(value) => { if (!value) close() }}><Dialog.Portal><Dialog.Overlay className="overlay" /><Dialog.Content className="drawer operation-drawer">
    <div className="panel-heading"><p className="eyebrow">Source inspector</p><Dialog.Close asChild><button className="icon-button" aria-label="Close operational evidence"><X size={18} /></button></Dialog.Close></div>
    <Dialog.Title>Operational evidence</Dialog.Title><Dialog.Description>{citation?.from}. This record belongs to the displayed episode, snapshot and cutoff.</Dialog.Description>
    {evidence.isPending && <Loading />}{evidence.error && <ErrorPanel error={evidence.error} retry={() => void evidence.refetch()} />}
    {data && <><div className="source-record-heading"><FileJson size={22} aria-hidden="true" /><div><strong>{data.event.service.name}</strong><span>{data.event.kind.replaceAll('_', ' ')} · {data.event.source}</span></div></div>
      <dl className="source-metadata"><dt>Occurred at</dt><dd>{utc(data.event.occurred_at)}</dd><dt>Available at</dt><dd>{utc(data.event.available_at)}</dd><dt>Episode</dt><dd className="mono">{data.episode_id}</dd><dt>Snapshot</dt><dd className="mono">{data.snapshot_id}</dd><dt>Cutoff</dt><dd>{utc(data.as_of)}</dd><dt>Source SHA-256</dt><dd className="mono">{data.artifact_hash}</dd><dt>Evidence ID</dt><dd className="mono">{data.evidence_id}</dd></dl>
      <h3>Recorded attributes</h3><dl className="attribute-list">{Object.entries(data.event.details).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{value === null ? 'null' : String(value)}</dd></div>)}</dl>
      <div className="section-label"><Fingerprint size={16} aria-hidden="true" /><h3>Source record</h3></div><p className="muted">The validated JSON record from the scoped source artifact.</p><pre className="source-code">{data.source_json}</pre>
    </>}
  </Dialog.Content></Dialog.Portal></Dialog.Root>
}
