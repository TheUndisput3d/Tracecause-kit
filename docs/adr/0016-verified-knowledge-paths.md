# ADR 0016 — Source-verified knowledge paths

Date: 2026-10-19. Status: accepted for the local portfolio follow-up.

## Finding

The projection already contained typed assertions, but runtime check selection
looked for a `**Check:**` string in a service-linked excerpt. The dashboard exposed
only service calls. Neither made the authored assertion's subject/support links
central to an inspectable query.

## Decision

Re-extract deterministic assertions and accepted service mentions from immutable
source bytes when reading projected documents. Compare exact quote, character
positions, predicate, extractor version, verification status, subject and all
supporting chunks with the scoped Neo4j projection. Reject mismatches. Ambiguous
or absent subjects do not become service-specific checks. This is deliberately
conservative: a paragraph near a service name does not automatically inherit it.

`GET /api/v1/knowledge` exposes a bounded heterogeneous evidence view. Each relation
carries evidence IDs. `GET /api/v1/knowledge/checks` joins a selected symptom service
through at most three directed CALLS hops to a verified SUGGESTS_CHECK assertion.
Breadth-first traversal yields one deterministic shortest path per service, handles
cycles and orders results by distance/service/ID. All inputs use the same episode,
snapshot, replay mode and cutoff. Limits are explicit (12 checks; display caps are
24 observations, 12 excerpts, 24 assertions, 256 relationships). Missing or truncated
paths are not interpreted as proof that a dependency does not exist.

This query returns documentary relevance with a proof path, not a causal inference
or a probability of failure. Authored historical cause reports remain authored
claims. The LLM cannot insert authoritative relationships or execute suggestions.
The investigation template is v8 because checks now require verified assertions;
existing reports and frozen evaluation artifacts keep their original versions.

## User interface

The evidence explorer offers a visual graph, an accessible relationship table,
quoted checks and individual citations for every call step. Temporal comparison
pins a real response and preserves each cutoff when opening its evidence. Only
matching episode/snapshot/replay scopes can be compared. Calls have a six-minute
lookback: an absent edge can reflect windowing, sampling or traffic.

## Evaluation and tradeoffs

Eight declared synthetic topology competencies compare direct service lookup with
the directed relation join. Real document parsing/linking/assertion verification
is used, while call IDs in that controlled evaluation are explicitly synthetic.
Database/API tests independently exercise real projection, source resolution and
corruption rejection. This measures query semantics, not diagnosis accuracy.

Source checks add read work; cached browser queries are scoped and views load on
request. The graph schema needs no migration. Infra assets, cross-incident causal
learning and probabilistic inference are not claimed. New relationships should be
added only with an actual ingestion source, temporal semantics and provenance.
