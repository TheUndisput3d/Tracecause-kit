# ADR 0017 — Operational sources, asynchronous paths and pinned incident history

Date: 2026-10-19. Status: accepted for the operational KG expansion.

## Context

The owner requested a deeper KG capstone: source-backed deployments, versions,
configuration, infrastructure and ownership; reasoning about changes preceding an
incident; comparison with previous incidents; and actual varied applications.
The earlier implementation primarily joined service telemetry to authored evidence.
More display nodes alone would not meet this requirement.

Keep immutable snapshots, scoped provenance and explicit uncertainty. Preserve
the frozen ranker/evaluation artifacts and existing saved reports. Operational
source data must not contain private experiment controls or expected targets.

## Decision: ingest typed operational receipts

Add one optional `operational_json` source to an episode's exact manifest
allowlist. Strict events cover deployments, configuration changes, runtime
inventory, ownership assignments and incident notes. Each event has an immutable
UUID, canonical service reference, occurrence/availability times, source name and
bounded typed details. Unknown or sensitive fields are rejected.

Import telemetry first and require matching service identities. Store the exact
bundle as a content-addressed artifact and reject attempts to replace an episode's
operational source membership. Build explicit application, deployment, software
version, runtime, host, configuration, team and incident entities, retaining
source evidence IDs on their relationships.

Reconstruct expected graph material from immutable sources before returning
operational results. Compare entity payloads, typed relationships, endpoints and
scope. Graph corruption must produce a failure rather than an unsupported claim.
Recorded operational state is descriptive evidence; a receipt is not proof that
its change caused an observed failure.

## Decision: distinguish asynchronous handoffs from calls

Retain the existing trace-derived synchronous `CALLS` semantics. Add typed
`MESSAGE_FLOW` only for a direct consumer-parent reference to a producer in the
same trace on another service. Verify both normalized spans against their exact
immutable artifacts and require bounded time ordering. Preserve the distinction
in proof paths and source inspection.

Do not infer a synchronous broker-to-worker edge, a delivery guarantee or a cause
from asynchronous parentage. The initial message rule is versioned and intentionally
narrow; tracing systems with other messaging/link conventions need a separately
specified and evaluated derivation rule.

## Decision: deterministic change and historical reasoning

Join recent deployment/configuration events to reachable service measurements and
latency/error degradation onset. Return explicit paths and citations. Changes
strictly preceding an onset bucket receive review priority; changes within that
ten-second bucket remain timing-ambiguous. Volume deviation alone does not
establish degradation.

Among otherwise preceding changes, prefer a changed service without further
observed degraded dependencies over an upstream changed service with degraded
descendants. Expose the downstream proofs. This transparent localization heuristic
can fail with concurrent faults, cycles, incomplete traces and sampling; it is
neither a learned probability nor a causal attribution.

Compare completed earlier episodes within the same workspace, application and
environment. Use declared weighted overlap of services, directed dependencies and
measured anomaly kinds, plus source-cited state differences. Unknown values remain
unknown. The earlier state is inspected at its own episode end and may contain
recovery; no automatic incident-phase alignment is claimed.

## Decision: pin history and preserve both clocks

Store exact earlier snapshot IDs in the new snapshot's immutable membership.
Include operational source hashes, derivation versions and historical IDs in its
content hash. Keep these fields conditional for episodes with operational sources
so legacy nonoperational snapshot hashes remain compatible.

Lock earlier `READY` snapshots while registering the new `BUILDING` reference.
Both `BUILDING` and `READY` historical references prevent retirement. Active
snapshots, readers, queued investigations and saved results retain their existing
protection. Operational/history readers renew their leases and fail if the lease
is lost before completion.

Event-time reads require events/measurements to precede their inspection cutoff.
Availability-time reads additionally require recorded availability. For historical
comparison under an availability-time current cursor, reconstruct the prior event
profile at its end only when the complete prior source availability is recorded
and no later than the current knowledge cutoff. Historical proof objects retain
their event inspection scope and the current `knowledge_as_of` boundary. Unknown
or future arrival information excludes the profile.

Pin the aggregate replay version and supported modes in the same membership.
Materialize every closed ten-second cursor through the final closed bucket;
request-time computation uses that identical cursor because it participates in
the aggregate identity. Materialize availability-mode observations separately
only when every source span has recorded availability. A missing capability is an
explicit API error. Upgrades verify the old projection, then publish new snapshots
in chronological order; they never rewrite old snapshot membership or invent
arrival timestamps.

## Decision: keep the model boundary explicit

The new operational and historical reasoning does not depend on LLM-generated
facts. Qwen continues its separately versioned role: selecting typed fact IDs from
the existing verified measurement/runbook catalog; code renders exact factual
content and validates citations. The new operational panels are deterministic.
Adding operational claims to the model catalog would require an explicit contract,
compiler and evaluation change rather than silently passing unstructured records.

## Capture and evaluation implications

Capture actual subprocess execution across three small applications: synchronous
chain, concurrent fanout and asynchronous queue. Retain real timestamps, HTTP
outcomes, direct producer/consumer linkage, deployed source artifacts and process
supervisor receipts. Two chronological episodes per application supply controlled
historical comparisons. Ownership remains an explicitly operator-assigned lab fact.

Keep intended controls and expected target event IDs in separate offline files.
Declare graph, chronological and direct-service comparisons using the same
eligible facts and candidate budget; retain negative results. The six captured
episodes demonstrate engineering behavior, not production diagnostic accuracy or
independent validation. Do not alter the original frozen benchmark to incorporate
these development cases.

## Consequences and alternatives

This adds useful domain joins while keeping every exposed relationship inspectable.
It also adds source-verification work, more pinned retention references and bounded
historical read cost. Explicit limits cover source size, events, message pairs,
graph size, traversal depth, change lookback, history membership and returned proofs.

Unrestricted model-generated Cypher or inferred deployment causality would weaken
the authority boundary. A visual graph populated without executed sources would
not establish the requested functionality. A broad infrastructure ontology without
an ingestion contract would make evidence ambiguous. The selected design extends
the domain only where actual source receipts and trace derivations exist.

Implementation details, diagrams and limits are in
[the operational architecture](../architecture/operational-graph.md). Release
verification and measured outcomes are recorded separately in
[the operational capstone report](../phase-reports/operational-capstone.md);
accepting this decision does not declare those gates passed.
