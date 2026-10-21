# ADR 0004 — PostgreSQL authority and redacted artifact staging

Date: 2026-10-10. Status: accepted for Phase 04.

PostgreSQL owns episode, job, span, artifact-reference and future snapshot state. A local SHA-256 repository stores immutable redacted normalized input; it does not retain original raw telemetry. The source hash and source locator remain in metadata, while exact retrievable evidence is the normalized span in the redacted artifact. This limits secret retention at the cost of losing raw-byte citation reconstruction. Redaction version changes create new artifacts and must be evaluated as new imports.

The import submission writes the artifact before its database transaction. Orphan artifacts are a documented recoverable result of a failed registration. The worker uses PostgreSQL row leases, `SKIP LOCKED`, at-least-once execution and semantic-key checks. It commits spans, episode `IMPORTED` status and job success together. It does not mark a graph snapshot READY. Future Neo4j writes and PostgreSQL active-pointer publication cannot be assumed atomic; Phase 06 must publish only after graph validation and reconcile abandoned builds.

An explicit operator retry can requeue a failed job with its original artifact and input hash. The queue does not silently generate a second job for a changed idempotency request. This avoids last-write-wins behavior and makes an import failure inspectable.
