# ADR 0005 — trace-derived signal semantics

Status: accepted for Phase 05 on 2026-10-11.

Use persisted redacted spans as the only input to versioned 10-second observations and 60-second analysis signals. Retain exact support references in `observation_spans`, store an immutable report artifact and derive IDs from a scoped input hash. Use SERVER spans for service request metrics, parent-backed cross-service SERVER calls for dependencies, and label CLIENT duration separately within a matched call. An unpaired CLIENT has no validated callee and creates no edge. Use a user-selected observable start instead of an injector timestamp. Enforce event/available cutoff modes, explicit baseline insufficiency, robust floor/cap formulas and deterministic text summaries. This keeps later graph construction rebuildable and makes non-graph retrieval eligible for the same signal evidence.
