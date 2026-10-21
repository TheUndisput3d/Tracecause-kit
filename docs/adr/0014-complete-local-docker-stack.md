# ADR 0014: Complete local Docker startup

Date: 2026-10-12. Status: accepted; verified locally.

The owner requested the entire application, including AI, start through Docker
build/up. The existing read-only release required manual migration, model prefetch
and operator profile selection. Add a standalone `compose.full.yaml` operator
stack with a separate project name, port and volumes. The existing release and
host development services remain independently usable.

Docker alone performs credential initialization, schema migration, model downloads,
model digest/schema verification, bundled ranker registration and idempotent demo
seeding. API/worker/web are gated on successful initialization. Qwen and embeddings
are runtime caches, not baked weights. Fixed image/model pins and lockfiles remain.

Random credentials are atomically created once in persistent Docker volumes. Admin
credentials use a separate volume absent from API/worker. One root bootstrap utility
owns only named volumes; applications retain UID10001, read-only filesystems and
dropped capabilities. Only loopback frontend is published. Operator login still
requires the generated token, and model output retains strict validation/fallback.

The full runtime includes the trained ranker for explicit comparison but keeps the
heuristic default. Frozen training/evaluation and fault-injection capture remain
explicit workflows; startup must not rerun research or change existing model weights.
Repeated startup validates a source/version fingerprint and saved citations before
skipping seed creation, preserving existing snapshots/results.

First startup requires network access to pinned images/model repositories. Any
initialization failure stops dependent services; no fabricated readiness or silent
model substitution. Persistent caches permit subsequent offline application use.

Actual cold downloads/startup, API generation, browser, upload and full stop/up
persistence passed. See [verification report](../phase-reports/docker-full-stack.md).
