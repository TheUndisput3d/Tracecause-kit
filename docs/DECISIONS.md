# Technical decisions

Phase 00 decisions are recorded in `docs/adr/0001-foundation.md`. The Phase 01 local-model configuration conflict is resolved in `docs/adr/0002-local-model-configuration.md`. Phase 03 strict OTLP JSON and allowlist redaction are recorded in `docs/adr/0003-strict-otlp-json-and-redaction.md`. Phase 04 PostgreSQL/artifact authority and lease processing are recorded in `docs/adr/0004-postgres-artifacts-and-lease-worker.md`. Phase 05 cutoff-safe signal rules and unresolved CLIENT behavior are recorded in `docs/adr/0005-trace-signals.md`. Phase 06 immutable graph publication and scoped reader leases are recorded in `docs/adr/0006-snapshot-graph-publication.md`. Phase 07 exact document offsets and assertion provenance are recorded in `docs/adr/0007-authored-document-provenance.md`. Revisit the dependency selection and package license inventory before a public redistribution.

Phase 12 durable capture resumption, conservative baseline boundaries, event-time replay and independent label review are recorded in `docs/adr/0009-captured-replay-and-resumption.md`.

Direct user authorization replaces the human-only review gate with explicitly labeled AI review; see `docs/adr/0010-user-authorized-ai-review.md`.

## Phase16

See ADR0012: durable uncertain-call fences, source membership for snapshot restore, saved-result/job retention, and separately versioned v6 grounding. Recovery deliberately fails closed when historical membership or provider restart cannot be proven. Runtime repair does not revise frozen evaluation findings.

## Phase 19

[ADR0013](adr/0013-final-audit-citation-offsets.md) records the corrected CLI default,
normalized-file byte pointers, versioned cache identity and immutable legacy reports.

## Complete Docker runtime

[ADR0014](adr/0014-complete-local-docker-stack.md) records automatic persistent
credentials, gated migration/model bootstrap, idempotent seed and the separate
local operator Compose stack with AI enabled.
