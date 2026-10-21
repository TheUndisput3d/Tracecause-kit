# Dashboard interaction guide

## Run locally

The dashboard uses real PostgreSQL/Neo4j and immutable artifacts. Start those services and migrate (`make up`, `make migrate`). From the repository root:

```bash
uv run --locked --group ml python -m scripts.dashboard.seed
uv run --locked --group ml python -m scripts.dashboard.serve
```

Open **http://127.0.0.1:15173**. The verification launcher binds the API to loopback18080 and the web server to loopback15173, with one durable worker. Runtime uses the non-superuser `tracecause_app` role; only the seed bootstrap/migration reads the admin secret. It uses a separate `tracecause_dashboard_v1` database and `reports/phase15-dashboard/` artifact/staging roots. The seed reads public fixtures/runbooks and two existing sanitized evaluation exports; it never reads private gold labels. It creates a private token file at `reports/phase15-dashboard/operator_token`. Enter that token in **Connect operator**; it stays only in browser memory. Ctrl-C stops the launcher and its children. These are local development services; Vite is not the production server.

The seed keeps existing immutable outputs. Two generated episodes and four method results use actual domain ingestion, signals, graph/retrieval and template generation. A separate quiet-cursor result exercises `no_anomaly_detected`. The nominal healthy fixture has threshold crossings at its final cutoff; that final-cutoff output is retained, not relabeled. The additional quiet cursor is an explicit UI-state fixture, not an accuracy result. Fixture IDs and exact cutoffs are in `reports/phase15-dashboard/fixture.json`.

For the optional real model track, first use the existing pinned local Ollama setup in `LOCAL_GENERATION.md`. Then launch with explicit opt-in:

```bash
TRACECAUSE_DASHBOARD_LLM=1 uv run --locked --group ml python -m scripts.dashboard.serve
```

This enables only the pinned local model and endpoint. Default launch remains template-only. No cloud model adapter or provider key is used.

## Workbench

- **Episodes:** filter public metadata by origin and page-local name/status. Counts describe the full recorded episode. Snapshot and availability warnings are separate from investigation findings.
- **Replay:** select an episode, snapshot/cutoff in the URL, ten-second slider or exact timezone-qualified UTC input. Reads debounce for300ms. Changing cursor/method never runs a model and marks the prior explanation stale. Queries include scope, method and safe configuration identity; aborted/older responses cannot replace the current graph.
- **Investigate:** connect an operator, choose retrieval/ranking/generation and optionally a symptom service, then explicitly Run. Learned ranking is offered only when a valid registered ranker is available. Jobs poll at1s, then2s after10s. Browser navigation does not cancel server computation; use Cancel to request cancellation at a safe checkpoint.
- **Inspect:** select a hypothesis or validated path to highlight its support. Graph arrows mean observed calls, not causal proof. The accessible table exposes the same source actions. Inspect a service's latest eligible ten-second bucket, or open any claim/check/edge citation. The evidence drawer shows exact source text, metrics, hash, interval and trace/span/offset references at the original snapshot/cutoff. Markdown has raw HTML disabled and is sanitized. Clipboard failure leaves visible IDs for manual copying.
- **Timeline:** SERVER p95 in milliseconds and error fraction, with a text/table alternative. Missing buckets remain null gaps. The shaded region is the actual five-minute baseline used by the signal rules; a shaded interval does not imply complete sampling.
- **Saved results:** permalinks and JSON export replay immutable artifacts. Original generation origin, cutoff, fallback reason and provenance remain visible. A saved-result GET reports replay even if its job has just generated a fresh answer.
- **Compare:** local operators can submit four template retrieval jobs with the same question/scope/budget. Saved IDs can also be compared read-only. Different scopes are flagged. UI timings/evidence overlap are diagnostic; quality metrics require an existing published evaluation artifact.
- **Evaluations:** synthetic and captured origins are separate. Null values remain undefined, absent report sections say NOT RUN, and per-split denominator/interval fields remain visible. Cards describe model/data limitations. Commit/hardware details omitted from the sanitized contract are explicitly identified as unavailable in that view.
- **Imports:** choose one manifest and all declared files. Local validation checks contract, baseline, byte limits, file counts and SHA-256 before upload. The server repeats validation and runs a durable import. Successful publication links to the real episode.

## Verification commands

With the local launcher running and pinned Node24 on PATH:

```bash
cd apps/web
npm test
npm run test:e2e
npm run test:e2e:llm   # requires the explicit local-model launcher above
```

Browser tests and screenshots live in `reports/phase15-browser/`. Real workflow tests use actual API/storage/worker/model behavior. The separately titled simulated boundary cases use Playwright interception or MSW only to exercise empty/error, stress and malicious-markup presentation; they are not database/model integration evidence. The real delayed-response test preserves actual backend response bytes while delaying delivery.

## Three-minute local demonstration

1. Explain the synthetic/captured labels and select a seeded episode.
2. Set an earlier UTC cursor, choose GRAPH and a symptom service; run a template investigation.
3. Move the cursor later and point out the stale explanation. Run explicitly again.
4. Select a validated path and open a source observation or runbook check. Explain why an observed call is not a proven cause.
5. Open a saved result and its provenance; export the same result without recomputing.
6. Compare method outputs and open the separate captured report. State the negative learned-transfer result and undefined ambiguous-test denominator.
7. Show the explicitly recorded quiet-cursor result and its empty hypothesis list. If using local generation, identify it separately and show its actual origin/fallback state.

Do not present saved template output as a live model call or a simulated stress screenshot as collected telemetry. Independent human review and production validation have not been performed.
