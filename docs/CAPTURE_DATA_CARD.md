# MiniShop captured telemetry data card

## Scope and status

This is a controlled five-service toy application, not a production incident corpus. The benchmark schedule is frozen in `configs/capture_schedule.json` (SHA-256 `c82dfba25c8ef1c74f7be79f883b57e604990f44fe9e8d3a6b7b0d21faa89992`). It declares 24 sequential runs: 20 faulty and four healthy, with 12 training, six development and six test runs. Each benchmark run specifies 300 seconds of baseline, 300 seconds of intervention and 120 seconds of recovery at approximately two storefront requests per second. `caller_timeout` is held out in the test split. All 24 captures passed the final source audit and AI review on 12 October 2026. The user delegated review to the implementation assistant (ADR 0010); review origin is AI, with no independent human adjudication.

The first short smoke run on 11 October 2026 is excluded from benchmark denominators. It recorded 24 traces and 528 normalized spans across storefront, checkout, payment, catalog and inventory, with no rejected spans, no missing parents, and 96 direct client/server pairs. Its baseline/intervention/recovery storefront responses were 6/6 healthy, 6/12 HTTP 502, and 6/6 healthy respectively. Its public source hash is `cc1315903f43ef86de9821a595e2f6f21145de9b9bb586d95fc0cd8a2acf6ecb`. These are smoke measurements only.

## Collection and contents

One small non-root Python image runs all five roles with FastAPI server and HTTPX client tracing. OpenTelemetry context propagates over actual HTTP calls; each storefront request receives an opaque generated trace ID that correlates its downstream spans without encoding the intervention. A pinned Collector 0.162.0 accepts OTLP HTTP on a private Compose network and exports JSONL files. The storefront alone publishes `127.0.0.1:18080`; no real product, payment, account, person or external application is involved. Inputs are four generated SKU IDs and quantities 1–5. Collector output is partitioned by root span start into complete traces; a request crossing a stage boundary remains with its root trace. Raw files remain ignored under `reports/capture/raw/`.

Each public `data/captured/<episode-id>/` bundle contains `manifest.json` and `traces.jsonl` with a byte count and SHA-256. `origin` is `captured_demo`. Public metadata contains no fault mode, split, target, seed or intervention time. `data/captured_private/<episode-id>/truth.json` contains those fields, actual stage intervals, load outcomes, quality measures and reset verification. Both trees are ignored by Git. A 22-span Collector conformance fixture at `data/fixtures/captured-collector/otlp.jsonl` demonstrates the actual wire encoding without publishing private truth.

Control routes require a token from a mounted local secret. They are excluded from FastAPI instrumentation. Fault names and control paths are checked against public OTLP before publication. The runner uses only fixed Compose services and a loopback storefront URL. An expiring in-memory control returns services to healthy state after a harness failure; normal and signal cleanup reset every role and verify the state.

## Quality and replay limits

The private audit computes per-stage server counts, HTTP 5xx rates and median latency. It records observed effects separately from intended injections. The timeout intervention changes checkout's caller timeout; a resulting failure alone does not identify payment as faulty. Such episodes remain ambiguous unless a reviewer accepts a target set from the trace evidence. Healthy and unobservable runs must not be silently converted into false positives or discarded.

The Collector file records export bytes, not independent per-span arrival times. `availability_recorded=false`; these bundles support **event-time replay only**. Collector batching and exporter queues mean 100% SDK sampling does not prove zero dropped spans. We measure normalized rejection, missing parent counts, service coverage, direct pair counts, observed throughput and actual phase durations. Collector or SDK drop counters are not yet independently recorded, so completeness beyond those observed checks is unverified. A missing service, rejected span, absent baseline, incomplete run or unobservable effect must remain visible in the audit.

The completed frozen corpus was used for the transfer comparison reported in EVALUATION_RESULTS.md. It is not evidence of performance on production outages, real commerce services, or an independent benchmark. No captured test run may be used to tune the model or choose thresholds and still be described as held out.

## 12 October review checkpoint

All 24 full-duration bundles are collected, source-audited, explicitly AI-reviewed and prepared through the actual evaluation databases. They cover five of each fault mode and four healthy controls. No automatic quality issues were reported. Capture controls were reset and all six containers stopped successfully. The five timeout cases remain unidentifiable; healthy cases have no accepted targets.

The first timeout run has checkout 5xx counts of 0/600 baseline, 537/600 intervention and 0/240 recovery. The inspected trace shows checkout 504/storefront 502 while downstream SERVER responses remain 200. Checkout is accepted as a triage target but unique cause is marked unidentifiable. Payment has 598 intervention SERVER spans and 1,438 direct pairs across the run, versus 1,440 for the other edges. This does not establish exporter loss or payment failure. Its reviewed CLIENT times out before the matching payment SERVER completes; their overlapping intervals and exact parent IDs still establish an observed call.

Separate explicit AI pair review covers 60 source-located CLIENT/SERVER pairs from five episodes/modes, twelve per episode across the three stages. All support their observed call direction. Healthy mode is represented. The selection is purposive, not a random accuracy estimate. Artifacts: `reports/phase13-captured-pairs/`.
