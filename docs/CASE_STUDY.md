# Case study: a temporal knowledge graph for inspectable incident investigation

## Problem

A service ranking or fluent explanation is difficult to trust without knowing
which observations existed at the selected time and which source supports each
claim. TraceCause combines traces and authored operational knowledge, then makes
the evidence paths and model boundaries inspectable.

## Architecture and engineering contribution

OTLP spans become normalized records and bounded signal windows. PostgreSQL and
content-addressed artifacts retain authoritative source membership. Neo4j projects
immutable, scoped service calls, observations, documents, excerpts and assertions.
Source quotes, exact offsets, entity links and assertion predicates are verified
against original bytes when read. Time cutoffs apply before retrieval and reasoning.

The expanded operational ontology also connects recorded deployments to software
versions, runtime instances and hosts; configuration writes to affected services;
ownership assignments to teams; and source-backed producer/consumer spans to
typed asynchronous handoffs. Three real local applications provide a synchronous
chain, parallel fanout and asynchronous pipeline: six chronological captures,
28,935 spans,129 operational receipts and900 producer/consumer pairs.

Change analysis joins recent receipts to observed dependencies and measured
latency/error onset. It exposes later, disconnected and healthy competing changes
as context. Historical comparison pins earlier same-application/environment
snapshots and explains version, configuration, host and ownership differences.
These results are deterministic temporal associations with exact source links;
they are separate from the optional LLM report's measurement/runbook fact catalog.

The functional KG query follows **symptom service → observed calls → downstream
service ← authored check → supporting excerpt**. Each call has telemetry evidence;
each check has an exact source quote and an unambiguous service subject. Traversal
is directed, cycle-safe and capped at three hops. The UI exposes proof paths,
heterogeneous relationships, accessible tables and comparison between two cutoffs.
Authored statements and observed calls are distinct; neither establishes causality.

Retrieval supports lexical, MiniLM dense, hybrid and graph methods. A trained
logistic ranker can score candidates, with the heuristic remaining default. Local
Qwen chooses allowed fact IDs through a constrained evidence plan. Server code
compiles the final prose and independently validates values/citations. Models cannot
create authoritative edges, issue database queries or execute infrastructure actions.

## What was measured

- Eight declared synthetic graph-query cases (chain, branch, cycle, disconnected,
  reverse and related topologies): 21/21 expected authored checks retrieved by the
  graph join; 8/21 by direct service lookup; zero extra checks. This is a controlled
  competency test, not a diagnosis benchmark. Real API/database corruption and
  citation checks are separate verification gates.
- Frozen v3 synthetic test candidate Hit@1: 10/10; exposed Hit@1: 5/10; answer
  coverage: 5/25. Captured development candidate Hit@1: 0/5, exposing poor transfer.
- Experimental v4, trained on 40 synthetic and 10 captured training episodes,
  reached captured development 5/5 but synthetic development fell to 8/10.
  These development-selected results are not an independent test evaluation.
- Final repeated evidence-plan diagnostics: 33/33 first-attempt validity/focus
  checks. Legacy diagnostics also passed 7/7; no comparative failure-rate reduction
  or production reliability estimate is claimed.
- The 100,000-span import reduced SQL calls from 40,223 to 423 with exact persisted
  row equivalence. Three retained runs took 96–98s versus an initial 105.5s; host
  variation means the reliable conclusion is fewer queries, not a universal speedup.

See [the operational capstone report](phase-reports/operational-capstone.md),
[the earlier portfolio audit](phase-reports/portfolio-readiness.md),
[reliability follow-up](phase-reports/reliability.md) and
[frozen evaluation results](EVALUATION_RESULTS.md) for artifacts and denominators.

## Delivery and limitations

One Docker Compose command starts frontend, API, worker, databases, MiniLM and
Ollama/Qwen. Durable jobs, bounded local inference, source inspection, saved replay,
real database/browser tests and tested restore/rollback make the application
reviewable beyond a notebook. The latest audit records exactly which gates ran.

The captures remain controlled local experiments. The original MiniShop data is
complemented by three public operational topologies, with only two incidents per
application and a single physical host. Authored runbooks are demo material.
Labels/review are AI-origin; no
independent operator study, production effectiveness or public deployment is claimed.
The larger ontology and actual captures broaden functional query verification;
they do not establish production generalization. These are research limitations
to disclose in a portfolio.

This project was implemented with Codex under the owner's direction. The owner set
requirements and delegated implementation/review. Do not present the work as sole
unaided authorship or the evaluation as independently human-adjudicated.

## Suggested portfolio description

“Built an AI-assisted incident investigation application with a temporal Neo4j
knowledge graph linking service traces, deployments, configuration and incident
history, source-verified multi-hop queries, FastAPI and React;
implemented constrained local-LLM evidence plans and reproducible retrieval/ranking
evaluation, including negative transfer findings; packaged the complete app in Docker.”

Use wording that accurately describes your role. Do not claim production users,
business savings or root-cause accuracy that were not measured.
