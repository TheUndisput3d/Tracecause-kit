# ADR0012: Durable generation fence and snapshot source membership

Status: accepted for phase16 local implementation. Review origin: AI, under the user's autonomous review authorization. No independent human validation.

## Decision

Keep the advisory lock inode stable and persist a minimal provider/start-time record before dispatch. A crashed or timed-out call leaves the record nonempty. Only a confirmed terminal reply or an inspected restart of that deployment's dedicated provider clears it. Model schema probes share the same artifact-root lock. Bound transport bytes and caller wall time independently of HTTP per-read timeouts. This sacrifices generation availability after an uncertain call to preserve the one-slot limit; deterministic template fallback remains explicit.

The automatic reconciliation command supports dedicated Compose providers. It checks an operator-owned deployment-project file, the service label and a start time after dispatch. It does not restart services or infer quiescence from loaded-model metadata. Native-provider reconciliation remains an explicit operator boundary and cannot silently clear admission.

Persist an immutable observation/document membership roster and document cutoff in PostgreSQL for each new snapshot. Recompute derived graph material from those source rows on restore and require the original content hash. Legacy rosters are backfilled only after source/hash/graph validation. A missing/corrupt old projection cannot be repaired by inventing its historical membership.

Pending investigation jobs and saved reports retain snapshots. Job submission locks the chosen snapshot so retirement cannot race admission. Direct investigation publication rechecks READY under the same row lock, preventing a result from being published against a concurrently tombstoned graph. Artifact publication syncs content and containing directories before SQL references are registered.

## Consequences

Backups require drained jobs and paused writers; staging is deliberately excluded. Restore needs compatible source/dependency versions, a new database, a new artifact root and an empty graph. Membership is structured snapshot metadata, not a competing store of computed metrics. Native shared-provider generation and cross-host workers do not obtain distributed admission guarantees.

Runtime template v6 is a separately versioned post-evaluation repair. It filters exposed candidates by their own adequate anomaly evidence, cites an explicitly labeled baseline example and narrows claim language. A new optional provenance field records its version; historical reports remain immutable and visibly unversioned. The frozen model, benchmarks and measured negative findings are unchanged.
