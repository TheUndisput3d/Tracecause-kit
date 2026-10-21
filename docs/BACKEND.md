# Backend, persistence and API specification

## B1. Selected runtime and packages

Use Python 3.12; `uv` for dependency locking; FastAPI/Uvicorn; Pydantic 2 and pydantic-settings; SQLAlchemy 2/Alembic/psycopg 3; official `neo4j` driver; NumPy/scikit-learn/sentence-transformers/CPU PyTorch; `httpx`; `structlog`; `prometheus-client`; `PyYAML` with safe loading; and `markdown-it-py` for Markdown parsing. Use stdlib hashlib, pathlib, datetime, uuid, json and concurrent-safe database transactions where adequate. HTTP retries may be implemented with a small tested helper rather than a new framework.

Development: pytest, pytest-cov, Hypothesis, Ruff, mypy, respx for HTTP test doubles, and jsonschema. Capture-only dependencies: OpenTelemetry API/SDK, OTLP HTTP exporter, FastAPI/httpx instrumentation and the pinned Collector image. Keep them in a separate dependency group. No Celery, Redis, Kafka, Airflow, Elasticsearch or managed service is required.

Use synchronous SQLAlchemy and the synchronous Neo4j driver inside FastAPI `def` routes and in the worker; do not block an async event loop with synchronous database/model calls. Long work always runs in the worker. Add `python-multipart` for bounded multipart uploads and keep the stream-level byte limit independent of the request Content-Length header. Use one Uvicorn process and one worker for the initial profile. A singleton model executor and explicit queue capacity limit local generation to one request at a time. Short API routes only validate, read metadata and enqueue work.

## B2. Module boundaries

`config/settings.py` loads validated environment/YAML with bounds and fail-closed secrets; `contracts/` holds Pydantic request/domain/result models; `ingestion/otlp_json.py` parses only documented OTLP/JSON inputs; `ingestion/markdown.py` preserves source offsets; `ingestion/redaction.py` enforces an attribute allowlist; `storage/postgres.py` owns sessions/transactions; `storage/artifacts.py` owns SHA-256 content-addressed writes; `jobs/` owns state transitions/leases; `telemetry/` owns buckets and anomaly calculations; `graph/` owns Cypher, projection and publication; `retrieval/` owns eligible-corpus retrieval; `ranking/` owns features/scorers; `investigation/` orchestrates bounded stages; `providers/ollama.py` implements local JSON generation; `evaluation/` is offline-only; `api/` contains thin routes/dependencies; `cli.py` provides the executable commands.

Core typed functions: `parse_otlp(source) -> ParseBatch`; `normalize_span(span, scope) -> NormalizedSpan`; `aggregate(spans, window) -> Observation[]`; `build_snapshot(episode_id) -> SnapshotBuild`; `select_eligible(scope, cutoff) -> EvidenceSet`; `retrieve(request) -> RetrievalResult`; `generate_candidates(request, retrieval) -> Candidate[]`; `score_candidates(candidates, features) -> RankedCandidate[]`; `assemble_bundle(...) -> EvidenceBundle`; `generate(bundle) -> Narrative`; `validate_result(result, bundle) -> ValidationReport`. None reads gold labels.

## B3. Authoritative relational tables

All IDs are UUIDs unless explicitly content-addressed. All wall-clock timestamps are timezone-aware UTC. Preserve original nanosecond span times as BIGINT; do not rely on PostgreSQL microsecond timestamps to preserve nanosecond precision. Foreign keys enforce ownership; queries also bind workspace/episode/snapshot scope.

`workspaces`: id, name, created_at. Seed exactly one default workspace; do not claim multi-tenant security.

`episodes`: id, workspace_id, display_name, origin enum, event_start/end, ingestion_status, active_snapshot_id nullable, data_quality JSONB, manifest_hash, created_at. Display metadata may not contain target/fault labels.

`source_artifacts`: id/hash, workspace_id, kind, content_hash UNIQUE, byte_size, relative_storage_key, schema_version, received_at, redaction_version, created_at. A separate reference table tracks episode/document consumers.

`spans`: composite unique key `(episode_id, trace_id, span_id)`; namespace/environment/service_key; parent_span_id nullable; start_ns/end_ns; kind; status; normalized attributes JSONB; source_artifact_id; source_record_offset; available_at nullable; rejection/quality flags. Index `(episode_id, service_key, end_ns)` and `(episode_id, trace_id, parent_span_id)`. Conflicting duplicates go to quarantine rather than last-write-wins.

`documents`: id, workspace_id, source_artifact_id, document_version, title, published_at, available_at nullable, document_type, metadata JSONB. Never overwrite a previously referenced version.

`chunks`: id, document_id, version, start_char, end_char, token_count, normalized_text_hash, embedding_artifact_hash and chunking_version. Exact excerpts are reconstructed from immutable document bytes; graph chunks are a rebuildable projection.

`observations`: id, episode_id, rule_version, service/caller/callee keys, observation_type, start_ns/end_ns, sample_count, error_count, p50_ms/p95_ms, attributes JSONB, provenance hash, available_at nullable. Support references use `observation_spans(observation_id, episode_id, trace_id, span_id, role)`; do not rely on an unbounded JSON array for all provenance.

`snapshots`: id, episode_id, graph_schema_version, input_manifest_hash, embedding_revision, status BUILDING/READY/FAILED/TOMBSTONED, created_at, published_at, error_code nullable. The active pointer update and READY status are one PostgreSQL transaction after graph verification.

`jobs`: id, workspace_id, kind, payload JSONB, payload_hash, idempotency_key nullable, status QUEUED/RUNNING/RETRY_WAIT/SUCCEEDED/FAILED/CANCELLED, next_attempt_at nullable, phase, progress counters, created_at, started_at, finished_at, lease_until, lease_owner, attempt_count, cancel_requested, result_reference, safe_error_code. Unique `(workspace_id, kind, idempotency_key)` where non-null. Terminal jobs are immutable except retention metadata.

`investigations`: id, workspace_id, episode_id, snapshot_id, job_id, request JSONB, request_hash, status, result_artifact_hash nullable, generation_origin, created_at. A saved result is immutable. Public reads never recompute it.

`model_registry`: id, kind, artifact_hash, feature_schema_version, training_manifest_hash, config_hash, metric_report_hash, created_at, promoted_at nullable. The trained linear scorer is a JSON artifact, not an uploaded pickle.

`evaluation_reports`: id, safe public artifact_hash, private run reference, dataset/split/model/config hashes, status and created_at. Only sanitized public reports are served.

`audit_events`: id, request_id, actor_type, action, resource_id, outcome, timestamp; no raw prompts, secrets or telemetry. Optional `reader_leases` are added for safe snapshot retirement. Migrations include constraints/indexes and are tested from an empty database.

## B4. Job implementation and transactional boundaries

Use a PostgreSQL queue with `SELECT ... FOR UPDATE SKIP LOCKED` to atomically claim the oldest eligible job; PostgreSQL documents SKIP LOCKED for queue-like consumers [S30]. Set lease to 30 seconds, heartbeat every 5 seconds and poll every 500 ms while idle. Keep claim and heartbeat transactions short; never hold a database transaction across embedding/model/network work. These are initial configuration values, not measured capacity limits.

There is at-least-once execution, not exactly-once delivery. Every handler must be idempotent using job IDs, input hashes, deterministic artifact names and immutable snapshots. A reclaimed job reuses or reconciles its existing output, not duplicate publication. Retry transient dependency failures at most 3 attempts with bounded backoff; malformed input and failed safety validation are not retryable. Check cancellation between batches and immediately before publication. A cancelled build cannot activate its graph.

Persist artifact bytes to a temporary path in the destination filesystem, fsync when appropriate, verify the hash, then atomically rename. Store references only after the file is present. A reconciliation command identifies orphan temporary files, orphan BUILDING graphs and database references with missing artifacts. Neo4j and PostgreSQL do not share a transaction; use publish-after-validation rather than pretending they do.

Worker stages for import: validate manifest/files -> redact/normalize -> persist spans/docs -> aggregate -> construct graph -> embed -> validate invariants/indexes -> publish -> mark succeeded. Investigation stages: validate -> freeze snapshot -> filter -> retrieve -> rank -> assemble -> generate -> verify -> persist. Stage-level elapsed times and safe counts are recorded.

## B5. HTTP contract and authorization

Use `/api/v1`; serialize JSON in UTF-8 with consistent enum casing. Unknown fields are rejected in write requests. Error shape: `{ "error": { "code": "...", "message": "safe text", "request_id": "...", "retryable": false } }`. Return UTC ISO strings for human time and integer nanoseconds as decimal strings in browser-facing JSON to avoid JavaScript integer precision loss.

Read routes: `GET /health/live`; `GET /health/ready`; `GET /api/v1/config` (safe feature flags only); `GET /api/v1/episodes?cursor=&limit=20&origin=`; `GET /api/v1/episodes/{id}`; `GET /api/v1/episodes/{id}/series?snapshot_id=&as_of=&temporal_mode=`; `GET /api/v1/episodes/{id}/investigations`; `GET /api/v1/investigations/{id}`; `GET /api/v1/investigations/{id}/export`; `GET /api/v1/jobs/{id}`; `GET /api/v1/graph?episode_id=&snapshot_id=&as_of=&temporal_mode=&focus_service_id=&max_hops=3`; `GET /api/v1/evidence/{id}?snapshot_id=&as_of=&temporal_mode=`; `GET /api/v1/evaluations`; `GET /api/v1/evaluations/{id}`.

Operator-only mutations: `POST /api/v1/imports`; `POST /api/v1/investigations`; `POST /api/v1/jobs/{id}/cancel`. `GET /api/v1/auth/check` verifies a bearer token without echoing it. Token comparison must use a constant-time routine, secrets are read server-side, and the UI stores the entered token only in memory. There is no client-side built-in secret. Public mode rejects every mutation server-side, regardless of valid-looking headers or hidden frontend buttons. Public access serves sanitized precomputed results only.

`POST investigations` accepts episode_id, optional snapshot_id, as_of, temporal_mode, question, optional symptom_service_id, retrieval_mode, ranking_mode and generation_mode. The server resolves and freezes a snapshot when omitted. It returns HTTP 202 with investigation_id, job_id and status. Client-selected generation modes are intersected with server-enabled modes; clients cannot set arbitrary models, URLs, context budgets or Cypher. Require an Idempotency-Key; reuse with the same canonical payload returns the same resource, different payload returns 409.

`POST imports` accepts multipart with a manifest and a bounded list of individually named `.json`, `.jsonl` and `.md` files. Do not accept compressed archives in version 1. Match logical names against the manifest; never honor client filesystem paths. Limit 25 MiB per file, 100 MiB per bundle and 100 files, with streaming checks before reading all content into memory. Validate content, not only extension. Reject symlinks, traversal names, remote URLs and undeclared files. CLI imports can have a separately configured higher bound for local captured bundles.

Pagination uses an opaque cursor over `(created_at,id)` with a server cap of 100. Return total only when cheap. Health readiness checks PostgreSQL and Neo4j; an empty dataset is not unhealthy. Missing optional Ollama weights is a `generation_degraded` capability, not a reason for all read APIs to fail. No provider traceback or database URI may appear in health responses.

## B6. Limits, errors and instrumentation

Initial limits: 2,000-character questions; 3 graph hops; 20 candidates; 80 displayed nodes; 120 edges; 60 intermediate evidence items; 12 final evidence items; 20,000 eligible retrieval texts; 10 queued jobs per operator; one running model request; 180-second generation deadline; 3-second graph-query timeout; bounded dependency retry. Exact limits are validated settings; query parameters cannot raise them. 400/422 signal invalid input, 401/403 authorization failure, 404 inaccessible/missing resource, 409 snapshot/idempotency conflict, 413 oversized upload, 429 queue/rate limit, and 503 unavailable required dependencies.

Log JSON with request_id, job_id, episode_id, snapshot_id, stage, duration_ms, counts, result status and safe error code. Expose internal Prometheus-format counters/histograms for request latency/errors, queue depth, rejected records, snapshot failures, retrieval time, generation time, invalid-output rate, fallback rate and evidence-validation failures. No public metrics endpoint with sensitive labels. Do not use episode/question text as unbounded metric labels.

## B7. Testing and nonfunctional requirements

Unit-test pure parsing, IDs, time filters, anomaly math, RRF, candidate features and validators. Hypothesis tests cover invalid timestamps, duplicate spans, hash stability, IDs and source paths. Integration tests run real PostgreSQL and Neo4j. Test transaction rollback, job lease expiry, duplicate imports, interrupted publication and graph rebuild. Contract tests compare OpenAPI, JSON Schemas and generated frontend types. E2E tests use the actual Compose stack and a fresh database.

Performance goals, not measurements: metadata GET p95 below 500 ms; a capped graph read below 1 second; template investigation p95 below 3 seconds on the documented small fixture/hardware; no fixed cross-hardware local-LLM latency promise. Report cold and warm model times separately and publish actual RSS/CPU. Bound memory before optimizing throughput. Version 1 is single-user/small-corpus; no production availability or horizontal-scaling claim.
