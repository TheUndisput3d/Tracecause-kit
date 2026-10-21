# TraceCause
## Evidence-grounded incident investigation with a temporal knowledge graph

**Complete product and engineering specification | Version 1.1 - open-source-first | 10 October 2026**

**Intended builder:** an AI/ML engineer with approximately two years of experience.  
**Delivery:** a reproducible, evaluated, read-only incident-investigation application, not an autonomous remediation agent.  
**Project status:** implementation blueprint. No application, training run, deployment, captured dataset, or benchmark result is represented as already completed.

## 1. Executive decision

Build an application that investigates failures in a small distributed application. It combines distributed traces, service dependencies, historical runbooks, and incident notes into an evidence-bearing knowledge graph. It ranks possible fault locations, explains the relevant call paths, retrieves supporting documents, and shows exactly which observations support each statement.

The central question is: **Which service or dependency should an engineer investigate first, what observable evidence supports that hypothesis, and what remains unknown?**

The flagship demonstration is a checkout incident. Several upstream services show errors, but the system must distinguish the service where errors are visible from the service or client configuration that may have initiated the failure. The graph helps investigate that distinction; it does not prove causality.

This project is deliberately more than a chat interface over documents. Its inspectable engineering contributions are a trace-to-graph pipeline, temporal filtering, provenance, entity resolution, evidence retrieval, a trained candidate ranker, a reproducible benchmark, and a usable investigation interface.

## 2. What the finished portfolio should demonstrate

**Data engineering:** parse a real telemetry format; validate and redact input; preserve lineage; handle duplicates, incomplete traces, and late records; publish consistent snapshots.

**Knowledge-graph engineering:** define an ontology; distinguish observations from assertions; create dependency edges from evidence; resolve service identities; perform bounded, time-aware graph traversal.

**Machine learning:** build features without label leakage; train a small interpretable ranking model; compare it with a hand-built baseline; evaluate using grouped splits and uncertainty estimates.

**LLM engineering:** retrieve a bounded evidence bundle; generate schema-constrained explanations; verify citations and numeric claims; abstain or fall back when evidence is insufficient.

**Software engineering:** typed interfaces, database migrations, durable jobs, API contracts, a graph UI, tests, CI, health checks, deployment and recovery documentation.

**Scientific judgment:** publish negative findings, distinguish synthetic from captured results, and never present graph connectivity as causal proof.

Do not claim production-scale incident reduction, real-world root-cause accuracy, autonomous operations, or business impact unless separately measured in a real setting.

## 3. User stories and acceptance surface

**US-01 - Investigate:** select an episode and an as-of time, describe a symptom, and receive up to three ranked hypotheses with supporting and conflicting evidence.

**US-02 - Inspect a path:** click a service or dependency and inspect the underlying trace references, time window, count, and observation type.

**US-03 - Replay:** move the time cursor and see only evidence available under the selected temporal mode. Later observations must not appear earlier.

**US-04 - Ask grounded questions:** ask about observed dependencies, error propagation hypotheses, related historical incidents, or missing evidence.

**US-05 - Compare methods:** inspect the same question using dense-only, hybrid, graph-expanded, and graph-plus-ranker retrieval; see context, latency and actual measured scores.

**US-06 - Import:** an authenticated operator imports a supported local artifact bundle, observes processing progress, and sees rejected-record diagnostics.

**US-07 - Audit:** open a reproducible evaluation report that identifies dataset hashes, split, model revision, prompt version, commit, environment and limitations.

**US-08 - Use without an API key:** replay the miniature fixture and receive a deterministic evidence report. This mode must be labeled as template generation, not an LLM response.

## 4. Scope and non-goals

### 4.1 Core release

Support one workspace, one known demo application, English documents, batch episode imports, OTLP/JSON traces, Markdown runbooks and incident notes, immutable graph snapshots, a small trained candidate ranker, local open-weight explanation generation with a deterministic fallback, and a browser interface. Compute latency and error indicators from traces; do not imply that these are independently collected infrastructure metrics.

The default runtime includes an API, one worker, PostgreSQL, Neo4j Community, and a frontend. A reverse proxy is added for deployment. The OpenTelemetry demo is a separate capture environment and is not required for every application startup.

### 4.2 Minimum vertical slice

Phases 00-09 produce an end-to-end CLI investigation from a fixture, through real database persistence and graph queries, to a cited deterministic report. This is the first useful milestone, not the full flagship release.

### 4.3 Flagship release

Complete phases 00-19, including the trained ranker, captured-telemetry track, full evaluation, UI, security checks and deployment verification. A fixture-only release may be useful but must not be described as telemetry-validated.

### 4.4 Explicitly excluded from version 1

No Kubernetes requirement; no Kafka, Redis, Airflow, Spark or multi-agent framework; no separate vector database; no graph neural network; no foundation-model fine-tuning; no arbitrary text-to-Cypher; no autonomous repair; no arbitrary URL fetching or PDF ingestion; no production customer data; no true multi-tenant security claim. These are not necessary to demonstrate the central engineering work.

## 5. Technology decisions

The application is open-source-first and runs without a paid model API. Use **Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, psycopg 3, Alembic, PostgreSQL 17, Neo4j Community, React 19, TypeScript, Vite, Tailwind CSS, TanStack Query, React Router, Cytoscape.js and Recharts**. These are deliberate baseline choices, not a claim that every version is the newest release. Choose a currently supported Neo4j Community release in phase 00 and validate its driver/index syntax. Use a supported Node LTS; Node 24 is the baseline. Exact patches and model revisions must be resolved and locked, not guessed.

The core Python ML stack is **NumPy, scikit-learn, sentence-transformers and CPU PyTorch**. Lexical retrieval uses scikit-learn TF-IDF, dense retrieval uses normalized MiniLM vectors, hybrid retrieval uses reciprocal-rank fusion, and graph retrieval uses bounded Neo4j traversal. A small corpus does not need a separate vector database. Neo4j vector/full-text indexes are an optional acceleration path, compared against exact eligible-corpus retrieval; they are not a reason to compromise time filtering [S05]. Do not install LangChain, LlamaIndex, Microsoft GraphRAG, graph-data-science plugins or multi-agent frameworks in version 1. The project's graph-aware retrieval, evidence contracts and scoring stay in project-owned code.

Use **`sentence-transformers/all-MiniLM-L6-v2`** for embeddings. Its model card specifies 384 dimensions and default truncation beyond 256 word pieces [S07]. Use 160-200 embedding-token chunks including title context, 30-token overlap, unit-normalized vectors and pinned model/tokenizer revisions. The optional later text reranker is `cross-encoder/ms-marco-MiniLM-L6-v2`; it is distinct from the trained service-candidate model and is not required [S08].

Use **Qwen3-4B-Instruct-2507**, locally served by **Ollama**, for explanation generation. The recommended quantized runtime tag is `qwen3:4b-instruct-2507-q4_K_M`; its published model artifact is about 2.5 GB, not the application's total RAM requirement [S17, S18]. Qwen's model weights have an Apache-2.0 license; Ollama is MIT licensed [S17, S20]. Describe this accurately as an open-weight model running on open-source software, not a claim that the complete training dataset/pipeline is reproduced. Use Ollama JSON-schema structured outputs and validate again with Pydantic [S19]. Only redacted evidence is sent to the local model. No OpenAI API key, paid embedding API or managed graph service is needed.

Neo4j **Community** is GPLv3; do not depend on Enterprise-only access control, multi-database behavior, online backup or graph algorithms [S21]. Use PostgreSQL for authoritative data and one durable worker rather than Redis/Celery/Kafka. Docker Engine is open source; Docker Desktop has separate subscription terms [S24, S25]. New application code may use Apache-2.0 while third-party notices remain separate; confirm redistribution obligations for the exact packaged components.

**Phase 00 version policy:** resolve compatible releases from primary documentation; commit `uv.lock` and `package-lock.json`; pin container digests and model revisions; record source URLs, verification dates and license identifiers in `docs/VERSIONS.md` and `docs/THIRD_PARTY.md`. No floating `latest` images, invented package versions, per-request downloads or unverified provider compatibility. Component-level libraries and responsibilities are specified in `docs/FRONTEND.md`, `docs/BACKEND.md`, `docs/AI.md` and `docs/DOCKER.md`.

## 6. Architecture and ownership

```text
Local OTLP/JSON + Markdown + manifest
                  |
       validate -> redact -> normalize
                  |
        immutable artifact directory
                  |
        PostgreSQL job + data registry
                  |
        aggregate -> construct graph
                  |
         validate -> publish snapshot
                  |
        retrieval -> candidate ranking
                  |
       bounded evidence bundle builder
                  |
       template OR local Ollama generation
                  |
      citation + numeric-claim validation
                  |
      FastAPI -> investigation dashboard

Evaluation-only truth -> evaluator/trainer
                    X never runtime retrieval
```

**PostgreSQL owns** episode metadata, normalized spans, aggregate windows, source/document metadata, jobs, active snapshot pointers, model registrations, investigation records, worker leases and sanitized evaluation-report metadata. Private gold manifests remain offline-only.

**The artifact directory owns** immutable redacted source bytes, manifests, training artifacts and evaluation outputs. Address files by SHA-256. The local volume is enough for version 1; an object-storage adapter is an extension.

**Neo4j owns** a rebuildable graph projection plus retrieval indexes. Losing Neo4j must not mean losing the authoritative input data.

**The backend owns** normalization, graph traversal, scoring, citation validation and access control. The browser never has database or provider credentials.

**The LLM owns no facts or actions.** It may express and organize an already assembled evidence bundle. It cannot create authoritative telemetry edges, execute Cypher, change the ranking order, alter time filters, or remediate services.

## 7. Repository specification

```text
tracecause/
  AGENTS.md
  pyproject.toml
  uv.lock
  Makefile
  .env.example
  compose.yaml
  apps/web/
  src/tracecause/
    api/              # routes, auth, request limits
    contracts/        # Pydantic domain models
    config/           # validated configuration
    ingestion/        # OTLP and Markdown adapters
    storage/          # PostgreSQL and artifact repositories
    telemetry/        # windows, signals, evidence summaries
    graph/            # migrations, writer, scoped queries
    retrieval/        # lexical, dense, hybrid, graph expansion
    ranking/          # features, training, model loading
    investigation/    # orchestration and evidence bundles
    providers/        # template, fake-test, local Ollama adapters
    evaluation/       # metrics, baselines, ablations, reporting
    jobs/             # durable worker and job handlers
    cli.py
  migrations/postgres/
  migrations/neo4j/
  configs/
  contracts/          # published JSON Schemas
  data/fixtures/      # small redistributable synthetic inputs
  data/gold/          # evaluation-only, never mounted in API
  scripts/capture/
  tests/{unit,integration,contract,e2e,security}/
  docs/{adr,runbooks,phase-reports}/
  prompts/            # the supplied phase instructions
  reports/            # generated outputs, not invented examples
  .github/workflows/
```

The supplied kit is a planning scaffold, not this implemented repository. Phase 00 creates the runnable structure while preserving the specification, prompts, and examples.

## 8. Data strategy and lineage

### 8.1 Track A: deterministic synthetic fixtures

Implement a seeded simulator that emits structurally valid OTLP traces with parent-child relationships, service identity, realistic timing variation, incomplete spans, duplicate delivery, clock-skew examples, distractor failures and healthy traffic. Synthetic data is for integration tests and controlled experiments, not evidence of production performance.

Generate 120 episodes across 24 scenario groups, five episodes per group. Fix a group-level split of 14 training groups, five development groups and five test groups: 70/25/25 episodes. A group is a topology template plus fault mechanism plus target role, not merely a random seed. Preserve four healthy groups, distributed two/one/one across train/development/test. Store the final exact assignment before tuning.

Reserve at least one topology template or mechanism variant for the synthetic test set. Keep repeated seeds, adjacent time windows, paraphrases and aliases of the same episode in one split. Log all deviations if the desired stratification is impossible. Never randomly split spans into training and test sets.

Introduce noise independently of the label. Do not encode the fault service in filenames, incident titles, span operation names, document headings, or scenario IDs accessible to runtime. Use opaque episode IDs. The simulator's private truth sidecar contains the target, injection interval and intended mechanism; runtime imports must never read it.

### 8.2 Track B: captured demo episodes

The **default capture source is MiniShop**, a deliberately small five-service application implemented in phase 12: `storefront -> checkout -> payment`, `checkout -> catalog -> inventory`. All services use one small Python image with different `SERVICE_ROLE` values and distinct OpenTelemetry service identities. Actual HTTP calls produce CLIENT and SERVER spans exported to a Collector. This is real instrumentation of a controlled toy workload, not production telemetry. The capture stack runs separately and can be stopped before running TraceCause.

Implement four controlled faults: payment HTTP 500 responses; inventory latency; catalog HTTP 500 responses; and an overly short checkout-to-payment client timeout. The last scenario is deliberately ambiguous in traces and must not be described as proof that the payment service failed. A private fault-control channel is isolated from telemetry and runtime retrieval. Controls must reset in `finally` on success, interrupt, failure and timeout. See `docs/CAPTURE.md` for exact routes, capture phases, assertions and label policy.

Target 24 captures: five independent runs for each fault family and four healthy runs. Capture an initial five-minute stable baseline, five-minute intervention interval and two-minute recovery; these are proposed settings, extended when warmup or completeness checks fail. Reserve one complete fault family for test. For the remaining 15 faulty runs use ten training and five development runs; distribute healthy runs two/one/one. This yields 12/6/6 captured episodes. Six test episodes are an external-to-the-simulator smoke check, not a strong generalization claim. Freeze the selected held-out family before inspecting results.

Verify actual observable symptoms, valid trace context propagation and a reset baseline. An intervention target is not automatically identifiable from traces. Store acceptable target sets and an `identifiable` annotation with a human rationale in the private evaluation sidecar. Exclude unidentifiable cases from unique-target accuracy while reporting them separately in abstention evaluation; never silently drop them.

**Optional second capture source:** use a pinned checkout of the OpenTelemetry Astronomy Shop demo, which provides configurable failure scenarios [S01, S02]. The documented full demo has its own resource requirements; it is not part of ordinary TraceCause startup [S13]. Candidate flags include `paymentFailure`, `paymentUnreachable`, `productCatalogFailure` and `intlShippingSlowdown`; check names/types against the pinned revision. Do not add this larger integration before the MiniShop track passes.

The Collector file exporter must use uncompressed newline-delimited OTLP JSON with an explicit writable volume. Its behavior is version-dependent; preserve a captured encoding fixture and verify the exact pinned exporter [S03, S27]. A generic Protobuf JSON serializer is not automatically an OTLP JSON serializer. Captures run only against these local demo services, never against production.

### 8.3 Documents

Author 20-30 short runbooks and historical incident notes describing the demo services and general failure patterns. Clearly mark authored demonstration material. Include conflicting hypotheses, outdated notes, service aliases and irrelevant passages. Each document has a publication timestamp and stable source ID.

Do not include a solution postmortem for the test episode in its retrieval corpus. Runbooks may state generic diagnostic procedures, but not hidden injection targets. Automated leakage checks and a human review must examine this boundary.

External documentation is optional and must have an explicit permitted-use/redistribution decision. The demo repository is Apache-2.0 licensed [S04]; inspect third-party notices separately. Do not assume a code license automatically licenses every externally linked document or dataset.

### 8.4 Manifest and data card

Every dataset manifest records origin (`synthetic`, `captured_demo`, or `external`), source revision, generator/capture version, seed when applicable, schema version, hashes, file sizes, time range, redaction version, license/attribution, completeness checks, split group and intended use. Gold labels live in a separate evaluation manifest unavailable to the API and worker during investigations.

The data card states known artifacts, unobserved signals, missingness, fault scope, sample sizes, and prohibited performance claims. No captured dataset is included in this planning kit.

## 9. Input formats, normalization and identity

Accept newline-delimited OTLP/JSON export requests, where each line is an object containing `resourceSpans`, and an optional single-request JSON file. Reject compressed archives in version 1 to avoid archive/path complexity. Support explicit Collector-native encodings only via named adapters and conformance fixtures, not permissive guessing.

OTLP trace/span IDs use hex representation in OTLP JSON [S03]. Normalize to lowercase, enforce expected lengths and nonzero IDs, preserve nanosecond timestamps as integers in Python and decimal strings at JSON boundaries, and never round them through JavaScript floating-point values. Normalize attribute values with their types; do not stringify arbitrary nested payloads.

A normalized span contains `schema_version`, `workspace_id`, `episode_id`, `source_id`, `trace_id`, `span_id`, `parent_span_id`, `service_key`, `span_kind`, `operation`, `start_ns`, `end_ns`, `status_code`, allowlisted attributes, and an exact source locator. Its deduplication key is `(workspace_id, episode_id, trace_id, span_id)`.

The canonical service key is a hash of the normalized tuple `(workspace, service.namespace, deployment.environment.name, service.name)`. Preserve the tuple as readable properties. Missing namespace/environment use explicit sentinels; never collapse services from different environments simply because names match. Support a small versioned alias registry with source references. Uncertain document mentions remain unresolved; the LLM cannot silently merge identities.

Reject negative duration, invalid IDs, invalid timestamp syntax, oversized lines, excessive nesting, and duplicate IDs with conflicting semantic payloads. Identical duplicates are counted and ignored. Conflicts are quarantined and surfaced, never silently overwritten. Treat out-of-order input as expected. Missing parents reduce completeness and may prevent edge inference; they do not justify inventing a dependency.

Attribute policy is allowlist-first: keep service identity, operation, span kind, selected HTTP/RPC status metadata and safe exception category. Drop authorization headers, cookies, query strings, request/response bodies, raw email addresses and tokens before persistence or provider use. OpenTelemetry explicitly warns that telemetry can contain sensitive data [S12].

## 10. PostgreSQL, artifact storage and jobs

Required relational tables and columns are defined in BACKEND section B3: workspaces, episodes, source_artifacts and references, spans, documents/chunks, observations and support references, snapshots, jobs, investigations, model_registry, evaluation_reports and audit_events. Service/dependency windows are typed observations; do not create a duplicate competing aggregate schema.

Use UUIDs or namespaced deterministic hashes for identities, UTC-aware timestamps for operational time, and BIGINT for nanoseconds within supported epoch bounds. Add foreign keys and uniqueness constraints. Index spans by episode/service/end time and episode/trace; aggregates by episode/window end; jobs by state/lease time; investigations by episode/creation time. Store structured metadata in JSONB only where a typed relational field is not warranted.

Job states are `QUEUED -> RUNNING -> SUCCEEDED`, with `RETRY_WAIT`, `FAILED`, and `CANCELLED` branches. A worker claims a job transactionally using a row lock and a lease. Store attempts, heartbeat, next retry time, input hash, phase checkpoint and sanitized error. Use at-least-once execution with idempotent handlers; do not advertise exactly-once processing.

A crashed worker's expired lease is recoverable. Cancellation stops at safe checkpoints; it must not publish partial snapshots. Use bounded exponential backoff for transient external failures and a finite retry count. Validation failures are not retried automatically.

Artifact writes use a temporary file plus atomic rename into a content-addressed path. Never derive filesystem paths directly from upload names. Refuse symlinks/path traversal. An artifact repository exposes read-by-ID, not arbitrary path reads. Runtime API responses never reveal host filesystem paths.

## 11. Telemetry aggregation and anomaly signals

Build non-overlapping ten-second buckets, represented as half-open intervals. The initial analysis window is the latest 60 seconds ending at a selected closed bucket boundary. Compute a baseline from the five minutes preceding the observable alert or selected investigation start. This is not the hidden injection start. If the baseline has insufficient traffic or too few buckets, return an explicit insufficiency flag.

Compute per-service server-span counts, explicit error counts, error fraction, p50 and p95 latency, and missingness/completeness. Preserve the distinction between `UNSET`, `OK` and `ERROR`. A versioned rule may classify a server span as erroneous from explicit ERROR status or a supported HTTP 5xx signal; an unset status is not an error and all HTTP 4xx responses are not automatically errors.

Use SERVER spans for service-observed request latency. When only CLIENT spans exist, create separately labeled client-observed dependency measures. Do not pool client and server durations into one service latency metric or double-count both as two requests. Compute quantiles from the relevant observations; never average bucket p95 values and call the result a window p95.

Derive a caller-to-callee edge from a valid cross-service trace parent relationship, preserving the referenced span pair. Walk same-service ancestors only under a bounded documented rule. A common trace ID alone is not sufficient evidence of a direct call. Avoid double counting the same call when both client and server spans are present. Async links and queues are out of scope for version 1 and must be identified as unsupported rather than misrepresented as synchronous calls.

Start with robust anomaly features: positive latency deviation using `(x - baseline_median) / max(1.4826 * MAD, floor)`; excess error fraction; request-volume deviation; time of first observed anomalous bucket; and completeness. Use floors of 5 ms for latency, 0.01 for an error fraction and one count for volume, with scores clipped to a documented maximum. These are starting hyperparameters to tune on development data, not statistically calibrated failure probabilities.

Store the exact formulas, baseline boundaries, bucket counts, input observation IDs and rule version with each derived evidence item. Show missingness instead of replacing unknown values with zero without a missingness feature.

## 12. Knowledge graph ontology

### 12.1 Node types

`Snapshot`: projection identity, episode, content hash, schema version, status and publication metadata.

`Service`: one snapshot-local representation of a canonical service, carrying `service_key`, namespace, environment and name.

`Observation`: a service or dependency measurement over a time window. Properties include observation type, start/end, counts, latency/error indicators, data-quality flags and an evidence ID.

`Incident`: observable symptom, affected service if known, selected time window and alert provenance. Hidden injector truth is not a node.

`Document` and `Chunk`: versioned text, publication time, source ID, offsets, embedding metadata and snapshot scope.

`Assertion`: a document-derived claim with subject, predicate, object/text, exact supporting quote offsets, extractor version and verification status. An assertion is not equivalent to an observed fact.

`Runbook`: an authored procedural document or a typed view of `Document`; use one representation consistently after an ADR.

### 12.2 Relationship types

`(Service)-[:CALLS]->(Service)`: materialized dependency, with snapshot ID and supporting observation IDs. Each traversal must confirm qualifying observations at the query cutoff.

`(Observation)-[:ABOUT_SERVICE]->(Service)`, `(Observation)-[:FROM_SERVICE]->(Service)`, and `(Observation)-[:TO_SERVICE]->(Service)` connect measurements to subjects.

`(Incident)-[:HAS_SYMPTOM_ON]->(Service)` expresses an observed symptom, not cause.

`(Document)-[:HAS_CHUNK]->(Chunk)`, `(Chunk)-[:MENTIONS]->(Service)`, `(Assertion)-[:SUPPORTED_BY]->(Chunk)`, and `(Assertion)-[:ABOUT_SERVICE]->(Service)` connect text to evidence and resolved entities.

`(Snapshot)-[:CONTAINS]->(...)` records membership. All project nodes and relationships also carry explicit `workspace_id` and `snapshot_id` where applicable for query enforcement.

There is no authoritative `CAUSES` edge. Store generated hypotheses in investigation results, not as telemetry facts.

### 12.3 Identity, provenance and indexes

Create uniqueness constraints on snapshot-local node IDs and indexes on snapshot/service keys and observation window ends. Node IDs must include snapshot scope so independent imports cannot merge accidentally. Do not store arbitrary nested maps as Neo4j properties; normalize them or store a typed JSON string only for non-query payloads.

The required reference implementation uses exact cosine over eligible 384-dimensional vectors and eligible-corpus TF-IDF, without a mandatory text/vector index. Graph constraints and scope/time indexes are required. If an optional full-text/vector adapter is enabled, build its declared indexes before publication and verify they are online; a successful database write alone does not prove index readiness. Model or dimension changes require a new embedding version and snapshot rebuild.

### 12.4 Temporal semantics

Every query specifies an episode, published snapshot and `as_of` cutoff. Retain only spans completed by the cutoff, aggregate buckets with `window_end <= as_of`, and documents whose `published_at <= as_of`. A CALLS edge is traversable only if it has at least one qualifying support observation within the permitted topology lookback. The default topology lookback is the baseline plus the current analysis interval.

Provide two explicitly labeled modes. `event_time_replay` reconstructs what completed events show by the cutoff. `available_time_replay` additionally requires a reliable source-availability timestamp. An offline import time is not a substitute for historical availability. When that timestamp is missing, refuse the stronger mode and mark the output retrospective.

Future documents, late-arriving observations and post-incident summaries must not leak into an earlier available-time investigation. Test this with fixtures where the answer changes after the cutoff.

### 12.5 Consistent snapshot publication

Register a snapshot as BUILDING in PostgreSQL. Write all Neo4j nodes under its new immutable ID. Run structural, provenance and index-readiness checks. Mark READY and atomically update the active pointer in a PostgreSQL transaction only after checks pass. Readers resolve the pointer once and carry that ID through the entire request. A failed build leaves the old snapshot active.

Cross-database operations are not atomic. Implement a reconciliation job that detects abandoned BUILDING snapshots and verifies READY snapshot existence. Deletion first tombstones the pointer, waits for active reader/job leases to finish, and then removes scoped graph nodes and eligible artifacts. Reference counting prevents deleting shared document artifacts prematurely.

## 13. Documents, extraction and entity resolution

Parse Markdown into heading-aware chunks. Target roughly 160-200 embedding-token pieces with approximately 30-token overlap, keeping title context inside the encoder limit. Preserve exact source text and character offsets; normalize only for retrieval, not for citation reconstruction.

Resolve service mentions using canonical names, a curated alias table and bounded exact/fuzzy matching. Log ambiguous matches for review. Measure entity-linking precision on an annotated small set. Do not use service-name similarity alone to merge graph entities.

The default extractor uses document metadata and deterministic alias matching. The optional LLM extractor returns only `subject`, allowed predicate, object/text, quote, start/end offsets and candidate entity IDs. Verify that the quote exists at the stated offsets and that entity IDs belong to the snapshot. Invalid output goes to review; it cannot create CALLS edges.

Allow document predicates such as `DESCRIBES_SYMPTOM`, `SUGGESTS_CHECK`, and `REPORTS_HISTORICAL_CAUSE`. A historical reported cause is evidence about that historical incident, not proof about the current one. Record extraction schema, prompt version, provider and model revision. Self-reported model confidence is not evidence of correctness.

## 14. Retrieval specification

All retrievers accept the same typed request: workspace, episode, snapshot, cutoff, temporal mode, question, optional symptom service and retrieval configuration. They return ranked evidence candidates with origin, score components, source locator and temporal eligibility.

**DENSE:** cosine retrieval over short document chunks and deterministic textual summaries of eligible telemetry observations. Telemetry must be available to the text-only baselines; otherwise the comparison unfairly gives graph methods extra data.

**LEXICAL:** fit a scikit-learn `TfidfVectorizer` over only the currently eligible texts and use cosine ranking. Preserve tokens containing service-name punctuation with a tested analyzer. This avoids hidden future-corpus statistics in strict replay. A Neo4j full-text acceleration adapter is optional; escape Lucene syntax and test it against the eligible-only reference.

**HYBRID:** retrieve up to 40 candidates per eligible modality and fuse with reciprocal-rank fusion, `sum(1 / (60 + rank))`. Ranks start at one; missing modalities contribute zero. Deduplicate by evidence ID.

**GRAPH:** take the same hybrid seeds plus the observable symptom service, resolve service identities, and expand at most three dependency hops. Cap service candidates at 20, returned nodes at 80, edges at 120 and supporting evidence records at 60. Preserve direction and qualifying support at every hop. Exceeding a cap produces a `truncated` warning, not a silent claim of completeness.

For version 1, use exact cosine over at most 20,000 eligible vectors by default: select scope/time first, then score and apply top-k. Vector-index filtering behavior varies with Neo4j versions. An optional ANN adapter may overfetch in bounded steps and fall back to the exact reference when underfilled. Never return cross-snapshot or future evidence because it ranked highly in a global approximate-nearest-neighbor query. Log which strategy was used.

The evidence builder selects at most 12 final evidence items and three support paths under a 6,000-input-token context budget. Reserve room for the schema, system instructions and response. Preserve at least one evidence item for every exposed hypothesis, include conflicting evidence where present, and disclose omitted/truncated material. These initial capacities are configurable but validated.

## 15. Candidate ranking: the ML contribution

The unit of prediction is an `(episode, cutoff, candidate service)` row. The output is a ranking of candidate services for investigation, not proof of the causal mechanism. Candidate recall and ranker quality are measured separately.

Features include service latency anomaly, excess error fraction, request-volume deviation, observed onset ordering, number/fraction of anomalous callers, dependency proximity to the symptom, downstream/upstream graph position, supporting trace count, missingness, and normalized retrieval support. Exclude service name, episode ID, injected target, flag value, scenario label and future observations. Never use final-episode aggregates at an early cutoff.

Implement a deterministic heuristic baseline first. Then train `SimpleImputer(add_indicator=True) -> StandardScaler -> LogisticRegression` as a pointwise relevance scorer. Weight each episode equally despite different candidate counts; optionally balance positive/negative rows within the episode. Fit preprocessing only on training data. Scikit-learn documents both leakage risks and group-aware cross-validation [S09, S10].

Use group cross-validation inside the training split; choose regularization and the abstention threshold on development data; evaluate the frozen test split once for the declared release candidate. With few groups, reduce folds and report the limitation rather than breaking group isolation.

Rank using decision scores. Do not label sigmoid output as a calibrated root-cause probability. Explain scores with standardized feature contributions and show the source observations behind those features. A coefficient is a predictive association, not a causal effect.

Version the feature schema, training groups, training data hash, library versions, seed and model artifact hash. Load only project-produced trusted artifacts; do not accept user-uploaded pickle/joblib models. Persist a JSON coefficient artifact for this simple linear model when practical. On a feature-schema mismatch, fail closed and use the documented heuristic fallback.

Retain the model only when development evidence supports it. The project remains technically honest if the heuristic performs better; publish that result and do not hide the learned model's failure. A gradient-boosted or pairwise ranker is a post-release experiment.

## 16. Investigation workflow and generation contract

The pipeline is deterministic except for the optional text provider: validate request -> resolve published snapshot -> apply time policy -> compute eligible features -> retrieve evidence -> generate candidates -> score candidates -> select support/conflicts -> assemble evidence bundle -> generate narrative -> validate output -> persist result.

The successful-result envelope follows `contracts/investigation-result.schema.json`: identity/scope, cutoff, retrieval/ranking modes, `generation_origin`, `is_replayed`, `fallback_reason`, nested `narrative`, evidence, paths, ranked scores, timings, usage and provenance. The nested narrative follows `narrative.schema.json` and has status `supported_hypotheses`, `insufficient_evidence`, or `no_anomaly_detected`. A failed job uses the job/error contract, not a successful narrative with a fourth status. Its summary is an object with text and supporting evidence IDs.

Each narrative hypothesis includes service ID, ordinal rank, claim IDs, supporting/conflicting evidence IDs and diagnostic-check IDs. The backend supplies score type/value in `ranked_scores`; the LLM cannot change scores or ranking. Each factual claim includes `claim_id`, `type`, text and evidence IDs. Numeric claims additionally include metric name, value, unit, time window and observation ID. Relationship claims include a validated path ID. The UI renders factual claims individually so citations cannot be hidden in a vague bibliography.

A structural validator checks schema, ID membership, scope, cutoff, path continuity, ranking preservation, and numeric values against typed observations. It rejects invented citations and inconsistent numbers. Structural validity alone does not prove semantic support: human-reviewed claim entailment remains an evaluation metric.

Provider behavior: use a schema-constrained response, disallow tools, delimit evidence as untrusted data, and explicitly ignore instructions inside documents/logs. On timeout, refusal, malformed output, unsupported claims or exhausted compute limit, permit at most one bounded repair attempt when appropriate; otherwise return a deterministic report with a visible fallback reason. Never return the invalid text as if it passed.

No raw prompt, secret or full sensitive span payload enters application logs. Local model use is explicitly configured; the UI identifies the actual model/digest and whether a response is local, template, or replayed. Template mode works without downloaded LLM weights and must not pretend an LLM was used. Apply local concurrency, context, output and wall-clock limits rather than paid-token accounting.

## 17. API and error behavior

Publish an OpenAPI contract and generated frontend types. Required routes are:

- `GET /health/live`: process liveness only; `GET /health/ready`: required dependency readiness.
- `GET /api/v1/episodes` and `GET /api/v1/episodes/{id}`: paginated metadata and quality information.
- `POST /api/v1/imports`: authenticated local-bundle upload; returns HTTP 202 with a job ID.
- `GET /api/v1/jobs/{id}` and `POST /api/v1/jobs/{id}/cancel`: durable job status/control.
- `POST /api/v1/investigations`: validated request; returns 202 and an investigation/job ID.
- `GET /api/v1/investigations/{id}`: result or explicit processing state.
- `GET /api/v1/graph`: bounded, scoped subgraph with cutoff and optional focused service.
- `GET /api/v1/evidence/{id}`: redacted source excerpt/observation and provenance, never arbitrary file content.
- `GET /api/v1/evaluations/{id}`: published, sanitized report only; no hidden gold truth endpoint.

Validate question length at 2,000 characters; upload size at 25 MiB per file; graph limits at or below server caps; and all enum fields. Use 400/422 for invalid requests, 401/403 for auth failure, 404 for inaccessible resources, 409 for unpublished/changed snapshots, 413 for oversized input, 429 for queue/rate limits, and 503 for unavailable dependencies.

Return a consistent error envelope: code, safe message, request ID and retryable flag. Preserve job failures instead of returning empty successful results. An idempotency key is bound to request payload and authenticated scope; the same key with a different payload is a conflict.

## 18. User interface specification

Build five primary views: an episode overview; investigation workspace; graph/evidence inspector; replay view; and evaluation report. Administration can be a minimal authenticated panel.

The investigation workspace places the question and time controls above a ranked hypothesis panel, a service graph, and an evidence drawer. Selecting a hypothesis highlights its support paths. Selecting a claim opens exact evidence. Every result shows snapshot, as-of time, generation mode, data origin and limitations.

Replay uses ten-second boundaries and shows the current eligibility mode. A request started at an earlier cursor position cannot overwrite a later result: cancel stale requests or match responses to request IDs. Keep graph positions stable across nearby replay times where possible.

Display call direction separately from hypothesized propagation. The legend distinguishes observed dependency, textual assertion, missing data and selected hypothesis. Do not use a red service node to mean "confirmed root cause." Use text labels and accessible patterns, not color alone.

Implement loading, empty, no-anomaly, insufficient-evidence, dependency-unavailable, compute-limit-exceeded and invalid-import states. Support keyboard navigation, semantic labels, visible focus, readable contrast and a text-table alternative to the graph. A mobile layout may simplify the graph but must preserve evidence access.

The evaluation view renders only generated results. Before a benchmark exists, show "not run" rather than seeded example percentages.

## 19. Evaluation design and honest reporting

### 19.1 Separate tasks

Measure dependency reconstruction and entity linking; evidence retrieval; candidate generation; candidate ranking; citation grounding; abstention; latency; and operational reliability. Do not compress these into one unsupported "accuracy" number.

For graph reconstruction, use synthetic known edges to measure precision/recall and separately hand-audit captured span pairs. For document linking, annotate a small mention set and report precision/recall and unresolved rates. For retrieval, author query/evidence judgments with labels 0/1/2 for irrelevant/useful/directly supporting. Use a review sheet with provenance and disagreements.

Create approximately 120 evaluation questions spanning direct lookup, dependency paths, temporal changes, related incidents, ambiguous cases and unanswerable cases. Assign every question to an episode and preserve its split. Automatically proposed questions require human verification. Do not treat another LLM's scores as unquestionable ground truth.

### 19.2 Fair baselines

Run five systems: anomaly-only heuristic; dense-only evidence retrieval plus the same generator; lexical+dense hybrid plus the same generator; hybrid+graph with heuristic ranking; and hybrid+graph with the learned ranker. The anomaly-only baseline has no generation requirement.

For retrieval comparisons, expose the same source information by indexing textual observation summaries for non-graph methods. Hold generator model, prompt, maximum context tokens, cutoff, corpus and decoding configuration constant. Log the selected evidence. A secondary oracle-context experiment may isolate generation quality, but label it as oracle and never compare its score as a deployable system.

Ablations remove graph expansion, temporal filtering, learned ranking and document context one at a time. The no-temporal-filter ablation is evaluator-only and must never be an unsafe runtime option. Keep gold labels out of all retriever/provider inputs.

### 19.3 Metrics

Candidate recall is the fraction of identifiable faulty episodes where at least one accepted target is generated. Hit@k is the fraction with an accepted target among the top k; report both end-to-end Hit@k and conditional-on-candidate-recall Hit@k. MRR uses the reciprocal rank of the first accepted target. Healthy and unidentifiable cases have separate denominators.

Retrieval uses Recall@k and nDCG@k on judged evidence; report judgment coverage and handle unjudged items explicitly. Grounding reports citation ID validity, numeric consistency, and human-judged citation precision/claim support. Report claim coverage separately from precision.

Abstention is evaluated on healthy, unanswerable and ambiguous cases; publish answered coverage and error rate among answered cases. Measure p50/p95 latency by pipeline stage, context size, provider usage, failure rate and cache hits. Report cold and warm runs separately with machine specifications and concurrency.

Compute paired bootstrap intervals by episode, not by individual spans or correlated questions. Use a fixed seed and 1,000 resamples as a starting configuration. For small captured samples, show exact denominators and wide uncertainty rather than claiming statistically established superiority.

### 19.4 Targets, not fabricated results

Hard correctness gates: zero cross-snapshot/future/gold leakage in tests; 100% valid exposed citation IDs; exact verified numeric claims; deterministic duplicate handling; no publication of partial snapshots; real database integration tests passing.

Engineering targets on a documented 4-vCPU/8-GiB application host: warm non-LLM investigation p95 below three seconds for the small reference dataset; graph endpoint p95 below one second; a 100,000-span batch import below ten minutes; a usable graph capped at 80 nodes. These are goals to measure and tune, not performance claims.

Research targets: seek better evidence recall or Hit@3 than hybrid-only at comparable context budget, with a clear latency/cost tradeoff. A negative or statistically inconclusive result does not justify changing the test split or faking completion. Publish the measured result and limitations.

## 20. Security, privacy and resource control

Use a same-origin frontend/API deployment. Default public mode is read-only over preloaded redacted demo episodes. Disable public uploads and all new generation calls. Local operator mode uses a server-validated bearer token kept only in browser memory. A future cookie-based mode would also require explicit CSRF protection. Never embed an administrative or provider key in frontend code.

Neo4j Community deployments must not be assumed to provide fine-grained database roles available only in other configurations. Isolate database ports on the internal network; enforce read-only Cypher transaction usage, allowlisted query templates and API authorization. Use stronger database RBAC only when the selected deployment supports it.

Escape/sanitize Markdown and disable raw HTML; render labels as text; reject path traversal and oversized payloads; restrict CORS; add request timeouts and bounded queries. No arbitrary Cypher, shell, model download URL or filesystem path may come from the client.

Use environment/secret-store credentials, excluded `.env` files, dependency and secret scans, and redacted structured logs. The local model receives only selected sanitized evidence; version 1 has no paid-provider integration. Do not claim a legal compliance certification.

Compute controls include per-request context/output limits, per-operator queue limits, one concurrent local-model call, a bounded wall-clock deadline and at most one format-repair attempt. Deny queue overflow atomically. Do not expose Ollama or arbitrary model/URL selection to public clients. No paid-token ledger or unknown-price integration is needed in version 1.

## 21. Testing and CI

Unit tests cover parsing, ID validation, precision-preserving timestamps, redaction, edge inference, aggregation, temporal eligibility, RRF, features, metrics, citation validation and compute limits. Property tests cover order invariance, duplication invariance, scope isolation and temporal eligibility. A fixed record becomes eligible after its end/availability time, but rolling-window retrieval membership need not be monotonic: old evidence can expire from the lookback.

Integration tests use real PostgreSQL and Neo4j containers. Verify constraints, migrations from an empty database, index readiness, worker crash recovery, concurrent idempotency, partial graph-build failure, active-snapshot stability and deletion/reconciliation. An in-memory mock is not a replacement for these tests.

Contract tests verify JSON Schemas, Pydantic models, API responses and frontend generated types. Browser tests cover selecting an episode, investigating, opening a citation, changing replay time, showing a graph path and displaying failure states.

Security regression fixtures contain instruction injection inside a runbook, attempted HTML/JavaScript, a Cypher-like question, a traversal filename, a future postmortem, a hidden target in metadata, and a fake credential. Assert that these cannot alter instructions, escape scope or reach the provider unredacted.

Required CI jobs: formatting/lint/type checks; unit/property/contract tests; real-database integration tests; frontend build and browser smoke tests; dependency/secret scanning; and a small fixture evaluation. Real local-model tests are explicitly tagged and require downloaded weights; they are clearly separate from mocked tests. Heavy captured-dataset experiments need not run on every commit.

Set a meaningful branch/statement coverage target for core deterministic modules, but do not game coverage with trivial tests. The final requirement is verified behavior and regression coverage, not a decorative percentage.

## 22. Runtime observability, deployment and recovery

Emit structured logs with request/job IDs, episode/snapshot IDs, stage duration, state transitions and sanitized error categories. Expose counters for imports, rejected records, investigation status, snapshot build failures, provider fallbacks, compute-limit denials and query truncation. Do not log full prompts or raw evidence.

Build container images with a non-root application user and pinned dependencies. Compose must provide health checks, internal networks, persistent volumes, resource limits and a startup path that does not assume sleeps equal readiness. Bind database ports to localhost only for explicit developer profiles; never expose them publicly by default.

For deployment, use one host with the frontend, API, worker, PostgreSQL and Neo4j behind TLS. Keep the separate telemetry-capture stack off that host where resources are limited. A planning allocation of 4 vCPU/8 GiB for template/replay mode and a 16 GB RAM development machine for local-LLM mode is an assumption to measure, not a guarantee. The OpenTelemetry demo itself documents separate memory/disk requirements [S13].

Back up PostgreSQL and immutable artifacts; rebuild Neo4j from a manifest when necessary. A backup is not proven until a restore into a clean environment passes a sample investigation and hash checks. Record backup retention and deletion policy.

Deploy by immutable image tag. Readiness must fail when required dependencies or the active snapshot are unusable. A release rollback restores a compatible application/image and active snapshot pointer. Database changes must have an explicit compatibility plan; never promise that arbitrary destructive migrations can be automatically reversed.

## 23. Developer commands to implement

```bash
make bootstrap          # install pinned local dependencies
make up                 # start application dependencies
make migrate            # apply SQL and graph schema
make seed               # import miniature fixture idempotently
make test-unit
make test-integration
make test-contract
make test-e2e
make check              # lint, types, core tests
make demo               # local seeded application, no paid model key
make eval-smoke         # deterministic miniature evaluation
make eval               # declared full evaluation, no invented scores
make train              # train from the declared training manifest
make capture-doctor     # verify pinned demo and local prerequisites
make capture            # explicit local capture, resets flags safely
make backup
make restore-check
make release-check
```

These are target commands for Codex to implement, not commands already supplied by the planning kit. Each must return a nonzero exit status on real failure. Missing infrastructure is reported as BLOCKED, not quietly skipped and counted as passed.

## 24. Phase roadmap and dependencies

00 foundation and compatible versions; 01 contracts and configuration; 02 synthetic data and frozen evaluation protocol; 03 ingestion; 04 persistence and jobs; 05 signals; 06 temporal graph; 07 documents and linking; 08 retrieval; 09 deterministic investigation; 10 local Ollama generation; 11 trained ranker; 12 captured demo data; 13 evaluation; 14 HTTP API; 15 dashboard; 16 security and recovery hardening; 17 CI and performance; 18 deployment; 19 portfolio handoff and independent audit.

Execute one phase at a time. Later phases may extend interfaces but cannot silently weaken earlier invariants. The prompts define prerequisites, files, implementation tasks, adversarial cases, verification and evidence-based completion.

A planning allowance is roughly 180-260 engineering hours for the complete flagship, including reviewing AI-written code, capture troubleshooting, annotations and portfolio preparation. A smaller usable MVP is approximately 90-140 hours. At 15-20 hours per week, the full scope is roughly 9-18 weeks; these are builder effort estimates, not guarantees. This is a scope estimate, not a promised delivery time. Cut optional model-extraction polish or extensions before cutting evaluation or provenance.

## 25. Definition of done and human ownership

A flagship release requires: a fresh-clone local demo; tested schema migrations; real graph and database persistence; valid replay filtering; a trained model artifact and model card; a separate captured-telemetry report; baseline/ablation reports; evidence-clickthrough UI; adversarial tests; deployment/restore evidence; and a limitations section.

A human must provide or approve machine permissions, model downloads, public deployment, external-data redistribution, and irreversible operations. The builder must inspect dataset labels, read representative generated code, verify benchmark claims and be able to explain the design in an interview. No prompt can guarantee that an AI agent autonomously produces a correct production system.

The agent may make reversible implementation decisions within this specification, recording ADRs. It must stop at genuinely blocked infrastructure or authorization boundaries, report exactly what is missing, and continue only independent safe work. A mock provider may test control flow; it cannot certify real-provider behavior.

## 26. Portfolio presentation

The README opens with one sentence, a genuine screenshot, a short demo link, the architecture, and a measured results table. Then explain the problem, why the graph is useful, quickstart, data provenance, evaluation method, failure cases, security boundaries and future work.

A three-minute demonstration should show: an incident selected at an early cutoff; hypotheses with limited evidence; a later cutoff revealing a stronger path; a clicked citation; the same question under a non-graph baseline; and an ambiguous case where the system declines certainty. Use saved results or replay only when clearly labeled; do not present recorded output as a live provider call.

Suggested project description: "Built TraceCause, a temporal knowledge-graph investigation system that reconstructs service dependencies from distributed traces, ranks fault-location hypotheses, and produces evidence-linked explanations. Evaluated graph-augmented retrieval against non-graph baselines using reproducible synthetic and captured demo incidents."

Only add numeric achievements after measurement. State dataset size, metric, baseline, evaluation split and hardware alongside every claimed improvement. Useful interview topics include parent-span edge inference, snapshot consistency, time leakage, candidate-recall ceilings, ranking versus causality, and the reason for template fallback.

## 27. Detailed implementation annexes

The following annexes are normative parts of this specification: `FRONTEND.md` defines every screen, state, component and browser interaction; `BACKEND.md` defines modules, persistence, jobs and API operations; `AI.md` defines prompts, models, algorithms and evaluation; `DOCKER.md` defines images, networks, volumes, resources and run modes; `CAPTURE.md` defines the small instrumented test application; `DEPENDENCIES.md` inventories package groups and locking rules; `ACCEPTANCE_MATRIX.md` maps requirements to verification gates. Starter configuration, schemas and deployment blueprints are supplied alongside them.

**MVP cut line:** implement phases 00-10, then the MVP subsets of 14 and 15, followed by local safety checks in 16. Use deterministic fixtures, the heuristic ranker, local generation and a simplified investigation/evidence UI. Defer the learned ranker, captured benchmark, comparison dashboard and public deployment. This is an explicitly smaller release and cannot claim the full flagship's validation. The normal flagship route still follows 00-19 in order.

## 28. Sources and implementation references

The source register in `docs/SOURCES.md` contains primary documentation, access date and the design decision each source supports. The specification's requirements, capacities and targets are proposed project decisions, not reported benchmark results or claims made by those sources.
