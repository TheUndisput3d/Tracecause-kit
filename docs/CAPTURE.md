# MiniShop capture specification

## C1. Purpose and service topology

Implement a small locally instrumented application so captured telemetry does not depend on running a large external stack. Five services share one image, with role-specific endpoints and `service.name`: storefront, checkout, payment, catalog and inventory. Runtime graph: storefront -> checkout; checkout -> payment; checkout -> catalog; catalog -> inventory. No database, message queue or real payment processor is involved.

Routes: storefront `POST /buy`; checkout `POST /checkout`; payment `POST /authorize`; catalog `GET /quote`; inventory `GET /availability`; each service `GET /health`. Requests contain only generated product IDs and integer quantities. Each request uses a generated opaque correlation ID that does not encode the scenario label. Use fixed synthetic products, no PII, card numbers or external network calls. HTTPX propagates W3C trace context; FastAPI/server and HTTPX/client instrumentation emit genuine spans. Exclude health checks and control routes from business telemetry.

MiniShop is not part of TraceCause's API code path. Put it in `demo/minishop/`, with its own image target and Compose file. A load-generator process sends a seeded mix at approximately 2 requests/second initially, with bounded concurrency. Track achieved throughput, failed sends and warmup; do not confuse configured rate with observed requests. Bound every downstream HTTP timeout and avoid automatic retries in the initial experiment, because retries change the fault semantics.

## C2. Fault definitions and controls

Healthy: downstream service work takes a seeded bounded 5-30 ms delay plus measured transport overhead. Payment-error: payment returns an HTTP 500 for 50% of approved requests. Inventory-delay: inventory adds 800 ms; callers have baseline timeouts above that delay so the latency is visible. Catalog-error: catalog returns 500 for 50% of requests. Caller-timeout: checkout uses a 10 ms timeout when calling payment while payment retains its healthy work distribution. These starting parameters must be adjusted only before dataset freeze if the effect is unobservable; record the reason and new values.

The harness uses operator-only `POST /__control` routes on an isolated local control channel/token, never routed through storefront. Controls are in-memory state with an expiration TTL, defaulting automatically to healthy. Do not export control requests, fault names, injector logs or flags as span attributes. Injection errors are test labels only. The runner validates the target Compose project and refuses a non-local base URL. No arbitrary shell payload or external service can be injected.

A finally/signal handler resets every control and verifies a healthy response, and TTL provides recovery when the harness itself dies. Unit tests for the harness simulate timeout/interrupt; one real interrupted short capture must demonstrate reset. Never depend only on a normal-success cleanup.

## C3. Capture workflow

Start the pinned Collector with OTLP HTTP receiver on internal 4318 and file exporter to a writable capture volume. Enable 100% trace sampling for the small controlled workload and record SDK/exporter queues. Do not claim complete telemetry merely from the configured sampler; validate observed parent completion and dropped/exporter counts. The file exporter is explicitly JSON/no compression; validate the actual output format [S27].

For each run: assign an opaque episode UUID; reset controls; warm services; open a fresh output file; establish 5 minutes of stable baseline; enable exactly one intervention for 5 minutes; reset; collect at least 2 minutes of recovery; force-flush SDK/Collector where supported; stop writes; copy/hash artifacts; validate trace structure and data quality; write public manifest and private truth separately. Healthy runs follow the same duration with no intervention, and public metadata does not identify that choice.

Do not mix previous-run buffered spans into the next capture. Partition by actual event interval with documented handling of boundary-crossing spans and wait/flush between runs. File receipt time is not necessarily per-span historical availability. Unless arrival timestamps are independently recorded, captured episodes support event-time replay only; stronger available-time replay must be refused.

Capture 20 faulty + 4 healthy runs under the predeclared grouped split. Run them sequentially; store parameters, commit, seed and actual intervals privately. Human audit confirms service mapping, intended intervention, observed effect and acceptable target set. Mark caller-timeout cases as ambiguous when traces cannot distinguish caller configuration from downstream delay. Do not relabel ambiguity as a model error or success selectively.

## C4. Deliverables and gates

Deliver the five-service app, its small load generator, Collector config, `compose.capture.yaml`, capture CLI, reset tests, a short verified encoding fixture, 24 captured bundles when completed, data-quality report, private labels and a public data card. `make capture-doctor` reports actual memory/disk/container availability. `make capture-smoke` runs a short non-benchmark healthy/fault/reset cycle. `make capture` runs the declared full protocol with operator approval.

Operator commands in the implementation are `make capture-doctor`, `docker compose -f compose.capture.yaml build storefront`, `make capture-smoke`, `make capture`, `make capture-status`, and `make capture-audit`. `make capture` resumes by skipping only frozen run keys with an existing private truth file. It does not shorten the declared 300/300/120-second stages. `make capture-reset` verifies every downstream role is healthy; `make capture-stop` resets before stopping the separate Compose stack. Run `uv run --locked --group capture pytest tests/capture` for optional SDK control tests. Public and private episode bundles and raw exports are ignored by Git; the Collector conformance sample is under `data/fixtures/captured-collector/`. The local capture data card is `docs/CAPTURE_DATA_CARD.md`.

The official OpenTelemetry Astronomy Shop is a later alternate source with separately pinned flags/images. It is useful for an independent application topology, but is not needed to declare MiniShop capture complete. Report MiniShop as a controlled toy benchmark; do not call it production validation or an industry RCA benchmark.

## Completed capture and review

The completed local resumed run used user service `tracecause-capture-final-20261012.service`; inspect it with `systemctl --user status tracecause-capture-final-20261012.service` and `make capture-status`. Progress is persisted at `reports/capture/runner_state.json`. CLI control mutations take an exclusive lock: do not start another capture or reset during this run. If the process stops unexpectedly, inspect the service/log and control state before resuming; completed frozen keys are validated, while incomplete raw exports remain outside the benchmark.

`make capture-review-packet` refreshes the audit and renders actual measurements and source links. Independent reviewers follow `docs/CAPTURE_REVIEW.md`. `make capture-review-gate` refuses incomplete/unreviewed datasets. Captured baseline ends round down to the previous closed ten-second boundary for runtime signal analysis, keeping intervention data out of the healthy baseline.

All 24 benchmark bundles are now complete and AI-reviewed; the capture stack is stopped. The final full audit and stop records are `reports/capture/final_audit.log` and `final_stop.log`. Independent human review was not performed.
