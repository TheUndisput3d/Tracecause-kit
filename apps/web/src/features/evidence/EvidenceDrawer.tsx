import * as Dialog from '@radix-ui/react-dialog'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import { api, query, type Models } from '../../lib/api/client'
import { ErrorPanel, Loading, utc } from '../../components/ui/common'
export type Citation = { id: string; snapshot: string; cutoff: string; temporal: Models['TemporalMode']; from: string }
export function EvidenceCitation({ citation, onOpen, label }: { citation: Citation; onOpen: (c: Citation) => void; label?: string }) {
  return <button className="citation" onClick={() => onOpen(citation)} title={citation.id}>{label ?? 'Inspect evidence'} <span aria-hidden="true">↗</span></button>
}
export function EvidenceDrawer({ citation, close }: { citation: Citation | null; close: () => void }) {
  const [copied, setCopied] = useState(false), [copyError, setCopyError] = useState(false)
  const evidence = useQuery({ queryKey: ['evidence', citation], enabled: !!citation, queryFn: ({ signal }) => api('Evidence', '/evidence/' + encodeURIComponent(citation!.id) + query({ snapshot_id: citation!.snapshot, as_of: citation!.cutoff, temporal_mode: citation!.temporal }), { signal }) })
  const data = evidence.data
  return <Dialog.Root open={!!citation} onOpenChange={(value) => { if (!value) close(); setCopied(false) }}><Dialog.Portal><Dialog.Overlay className="overlay" /><Dialog.Content className="drawer">
    <Dialog.Title>Evidence inspector</Dialog.Title><Dialog.Description>{citation?.from} · Sources at the selected snapshot and cutoff.</Dialog.Description><Dialog.Close asChild><button className="close">Close evidence</button></Dialog.Close>
    {evidence.isPending && <Loading />}{evidence.error && <ErrorPanel error={evidence.error} retry={() => void evidence.refetch()} />}
    {data && <><span className="badge">{data.kind.replaceAll('_', ' ')}</span><h3 className="mono">{data.evidence_id}</h3>
      <dl><dt>Snapshot</dt><dd className="mono">{data.snapshot_id}</dd><dt>Cutoff</dt><dd>{citation && utc(citation.cutoff)}</dd><dt>Source SHA-256</dt><dd className="mono">{data.locator.source_hash}</dd><dt>Interval / publication</dt><dd>{data.interval ? `${utc(data.interval.start)} – ${utc(data.interval.end)}` : data.published_at ? utc(data.published_at) : 'Not recorded'}</dd><dt>Availability</dt><dd>{data.available_at ? utc(data.available_at) : 'Not recorded; event-time eligibility only'}</dd></dl>
      <h3>Source excerpt</h3><div className="source-quote"><ReactMarkdown skipHtml rehypePlugins={[rehypeSanitize]}>{data.text}</ReactMarkdown></div><details><summary>Exact excerpt text</summary><pre>{data.text}</pre></details>
      {!!data.metrics.length && <table><caption>Recorded metrics</caption><thead><tr><th>Metric</th><th>Value</th><th>Unit</th></tr></thead><tbody>{data.metrics.map((m) => <tr key={m.metric_name}><td>{m.metric_name}</td><td>{m.value}</td><td>{m.unit}</td></tr>)}</tbody></table>}
      <dl><dt>Trace</dt><dd className="mono">{data.locator.trace_id ?? 'Not applicable'}</dd><dt>Span references</dt><dd className="mono">{data.locator.span_ids.join(', ') || 'Not applicable'}</dd><dt>Source offsets</dt><dd>Normalized file byte {data.locator.record_offset ?? '—'}; characters {data.locator.start_char ?? '—'}–{data.locator.end_char ?? '—'}</dd></dl>
      {data.quality_flags.map((flag) => <p className="notice" key={flag}>{flag}</p>)}
      <button onClick={async () => { try { await navigator.clipboard.writeText(`${data.evidence_id} | snapshot ${data.snapshot_id} | cutoff ${citation!.cutoff}`); setCopied(true); setCopyError(false) } catch { setCopyError(true) } }}>{copied ? 'Citation copied' : 'Copy citation'}</button>{copyError && <p role="status">Clipboard unavailable. Select and copy the evidence ID, snapshot and cutoff above.</p>}
    </>}
  </Dialog.Content></Dialog.Portal></Dialog.Root>
}
