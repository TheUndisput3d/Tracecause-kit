# Security boundary and threat model

TraceCause is a single-operator research application with redacted demonstrations. This is not a production security certification. Source-backed communication edges and ranking scores are not causal proof.

## Assets and trust boundaries

| Input or asset | Threat | Enforced boundary |
|---|---|---|
| Multipart uploads | Traversal, archives, URL fetches, excessive allocation | Operator bearer authorization; server UUID staging; flat declared names; extension, SHA and byte checks; 25 MiB/file, 100 MiB wire bundle, 100 files; no archive extraction or remote fetch |
| JSON requests | Deep nesting, oversized bodies, parser errors | 64 KiB wire cap and streaming depth cap 64, including chunked requests; strict typed contracts; safe error envelopes |
| Telemetry attributes | Credentials/PII, misleading measurements | Attribute allowlist and redaction before persistence; bounded OTLP parsing; original source hashing; server/client separation and qualified parent relationships |
| Authored Markdown and questions | Prompt injection, HTML/script execution, invented graph mutations | Untrusted text remains data; sanitized Markdown with raw HTML skipped; no model tools; fixed draft claims/IDs/metrics/order validated after generation |
| Graph and SQL reads | Scope escape, query injection, excessive traversal | Bound query parameters; allowlisted query shapes; eligible corpus filtering before retrieval; explicit snapshot/cutoff/workspace; graph caps and transaction timeouts |
| Model artifacts/providers | Pickle execution, remote inference, oversized output, concurrent work | Validated JSON ranker; no untrusted Python deserialization; pinned local model digest/tokenizer; endpoint allowlist; bounded transport bytes, wall time, context and output; durable generation fence |
| Artifacts/snapshots | Partial writes, tampering, stale graph membership, deletion during replay | Content hashes, restrictive paths and symlink refusal; file and directory fsync; immutable source membership; validate before publication; pending jobs/readers/saved results prevent retirement |
| Operator credentials | Browser persistence, logs, CSRF | File-backed secrets, constant-time bearer check, in-memory browser token; no cookie authentication; no cross-origin CORS grant; request logs contain safe IDs/stages only |
| Public demonstration | Unauthenticated compute or mutation | Server rejects all writes before reading their body; saved reports only; no public model proxy, trainer, gold or arbitrary query endpoint |

The host administrator, database administrator and Docker operator are trusted. A compromised administrator can change sources, mounts or provider identity. File locks protect one deployment sharing one artifact root on one host; they are not distributed locks. CLI model probes must use the worker's `--artifact-root`. Uncoordinated applications sharing a native Ollama process are outside the one-slot guarantee; use a dedicated container for controlled resource measurements.

## Model crash handling

The generation lock contains a synced, nonempty record **before** sending a chat request. A confirmed terminal response clears it. Timeout, malformed/nonterminal transport output or SIGKILL leaves admission closed, including schema probes. A transport thread cannot clear the record after the caller times out. The caller has a wall-clock deadline even if the peer trickles its response; a bounded daemon transport may remain until disconnect/provider restart, with further calls blocked.

`generation_reconciliation_required` triggers visible template fallback. Restarting only the worker does not clear it. The recovery tool verifies the dedicated provider container's restart time and deployment project before writing a receipt and reopening admission. It never automatically restarts a provider. Native-provider restart reconciliation is not automated; preserve the fence and use template mode pending an operator-reviewed recovery. Do not delete or rotate the lock file to bypass the fence.

Ollama metadata readiness is not proof of an empty generation queue. `/api/ps` and elapsed time are not used as restart proof. Real crash evidence is in `reports/phase16-model-crash-v1/report.json`; it used an isolated cached Ollama 0.40.3 diagnostic image and the pinned Qwen weights. It is not verification of the release runtime image.

## Database and browser limits

PostgreSQL runtime uses a non-superuser role without role/database creation. Migration/backup tools explicitly use separate admin credentials. Neo4j Community does not provide the fine-grained Enterprise RBAC assumed by some deployment designs: the application credential can mutate its database. Query separation, a private deployment network, one authoritative writer and scoped validation are application controls, not database-enforced tenant isolation.

The development database ports are loopback-bound. `compose.internal.yaml` removes database host mappings and development-network membership for container deployments. The complete release stack and external exposure checks belong to phase18. No internet-facing deployment is authorized or claimed. Public mode alone is not a firewall.

Bearer credentials are never sent automatically by a browser, so cookie CSRF tokens are not used. There is no authentication cookie or credentialed cross-origin allowance. The production proxy must enforce the documented CSP/nosniff policy; Vite is a local development server. Frontend build assets must pass the secret scan before release.

## Grounding and historical results

Runtime template v6 only exposes candidates with adequate measurements that meet the existing anomaly rule. Ranking weights are unchanged. A cited chronological middle baseline bucket is explicitly an example; it does not establish the whole baseline distribution, a statistically proven change or causality. Historical results have an explicit UI notice and remain immutable. Frozen evaluation outputs and their negative findings are preserved; this repair is a post-evaluation application change, not a new held-out accuracy result.

## Reporting and limitations

Report a reproducible issue locally with safe resource IDs and error codes. Do not attach operator secrets, full prompts or raw sensitive telemetry. No public security-contact address or response SLA is claimed. Public hosting, cross-host failover, malicious-host containment and production penetration testing have not been verified.
