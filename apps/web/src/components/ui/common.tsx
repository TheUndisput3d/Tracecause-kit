import { Link } from 'react-router-dom'
import { ApiFailure, type Models } from '../../lib/api/client'
export function ErrorPanel({ error, retry }: { error: unknown; retry?: () => void }) {
  return <section role="alert" className="notice error"><strong>Request could not complete</strong>
    <p>{error instanceof Error ? error.message : 'The service is unavailable.'}</p>
    {error instanceof ApiFailure && <small>{error.code} · Request {error.requestId}</small>}
    {retry && <button onClick={retry}>Try again</button>}</section>
}
export function OriginBadge({ origin }: { origin: Models['DataOrigin'] }) {
  return <span className={`badge origin-badge origin-${origin}`}><span aria-hidden="true" />{origin === 'synthetic' ? 'Synthetic' : 'Captured demo'}</span>
}
export function SnapshotBadge({ id }: { id: string | null }) {
  return <span className="mono" title={id ?? undefined}>{id ? `Snapshot ${id.slice(0, 8)}` : 'No published snapshot'}</span>
}
export function LimitationsPanel({ items }: { items: string[] }) {
  return items.length ? <aside aria-label="Result limitations" className="notice"><strong>Limits of this evidence</strong><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul></aside> : null
}
export function Loading({ text = 'Loading saved data…' }: { text?: string }) { return <p className="loading-state" role="status"><span className="loading-indicator" aria-hidden="true" />{text}</p> }
export function Empty({ text }: { text: string }) { return <div className="empty"><p>{text}</p><Link to="/">Browse episodes</Link></div> }
export function utc(value: string) { return new Date(value).toISOString().replace('T', ' ').replace(/(?:\.000)?Z$/, ' UTC') }
