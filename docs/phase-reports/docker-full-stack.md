# Complete Docker operator stack — implementation and verification

Requested after phase19: start the entire frontend/backend/worker/KG/local-AI runtime
through Docker build/up, with no separate host runtimes or manual bootstrap sequence.

## Changes

- Standalone compose.full.yaml with six persistent runtime services and four gated
  startup jobs; pinned image defaults work without a host .env file.
- Named-volume credential bootstrap with atomic creation and stable passwords.
  PostgreSQL admin secrets are isolated from API/worker.
- Full startup downloads pinned MiniLM/tokenizer/Qwen, verifies the model digest and
  actual structured generation, migrates SQL/Neo4j, seeds demo/runbooks/reports and
  registers the bundled experimental ranker. Heuristic remains the default.
- Seed fingerprint and citation verification preserve existing snapshots on restart.
- Docker-only Make shortcuts, README quickstart, full operations guide and ADR0014.
- Unit boundary tests plus actual container/API/browser/restart verification scripts.

## Verification — PASS locally, 2026-10-12

All paths below are under `reports/full-stack-v1/`. Commands ran from the repository
root; host verification used the existing Python environment and Node24. Running the
application itself requires only Docker Engine/Compose. Previous release/development
services were left independently available.

| Gate | Actual result and evidence |
|---|---|
| First startup | `docker compose -f compose.full.yaml up --build -d --wait --wait-timeout 2400`, exit0; `up.log`. New database, secrets, artifact and model volumes; actual MiniLM/tokenizer/Qwen downloads, without copying host model caches. |
| Final images | Same command, exit0; `up-final.log`. Bundled ranker registered, existing seed reused. Six runtime services healthy, four startup jobs exited0. |
| Configuration | `docker compose --env-file /dev/null -f compose.full.yaml config --format json`, exit0; `compose-defaults.json`. No host credential files or environment configuration required. |
| Regression | `UV_NO_SYNC=1 make check`, exit0; `check-final.log`: 248 Python tests, four Vitest tests, Ruff, mypy (81 source files), contracts, frontend lint/types/build passed. Existing dependency deprecation and bundle-size warnings remain. |
| Real API/AI | `.venv/bin/python scripts/deployment/full_smoke.py --output reports/full-stack-v1/smoke`, exit0; `smoke/report.json` and three complete responses. Auth, hidden paths, graph, queued jobs, idempotency and citations checked. |
| Browser | `node scripts/deployment/full_browser.mjs reports/full-stack-v1/browser-v2`, exit0; operator login, graph workbench, generation selector, submitted job and citation drawer. No JS errors or external browser requests; three screenshots retained. |
| Upload/restart | `.venv/bin/python scripts/deployment/full_lifecycle.py --output reports/full-stack-v1/lifecycle`, exit0. Actual multipart upload published a new graph through the worker; then stopped the stack and ran full build/up again. |
| Persistence/boundaries | `lifecycle/report.json`: same operator credential, two unchanged active snapshots, all five saved response hashes unchanged. Six healthy runtime containers, four successful startup jobs, only loopback web published; app UID10001/read-only, no admin volume, API artifacts read-only. Actual image IDs retained. |
| Security scans | `.venv/bin/python -m scripts.ci.scans --output reports/full-stack-v1/scans`, exit0. Python/npm dependency audits and Git/working-tree secret scans all exit0. |

Real queued investigations used these modes:

| Retrieval / ranker / generation | Observed result |
|---|---|
| GRAPH / heuristic / template | Supported hypotheses; six citations; 6.453s including queue/polling. |
| GRAPH / learned / template | Honest insufficient-evidence result; zero citations; 2.014s. No claim of learned superiority. |
| LEXICAL / heuristic / Ollama | Supported hypotheses; three citations; 74.153s including queue/polling; 1,626 input and 583 output tokens; no fallback. |

Ollama0.40.2 used the pinned Qwen digest
`sha256:0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`.
Cached restart reran a real schema probe without another model download. Bundled
ranker SHA remains `a824ecaef074ec37402b0caa7bf880c92929e57171b973ff2e0e9e72feec277f`.
These are functional diagnostic runs, not a new research benchmark or reliability
estimate. Model output can still fail validation and visibly use a template.

### Initial failure retained

The first browser script logged in before using `page.goto` to the workbench. A full
reload correctly discarded the in-memory operator token, leaving Run disabled; the
script timed out waiting for a POST. `browser.log` and `browser/` retain the failure.
The verification script now logs in after navigation. No application authentication
behavior or acceptance assertion was weakened; `browser-v2/` is the passing run.

## Operational handoff

Complete operator stack remains running at **http://127.0.0.1:14000** under project
`tracecause-full`. Use [the full Docker guide](../DOCKER_FULL_STACK.md) for startup,
token login, explicit Ollama selection, logs and persistent stop/restart. ADR0014
records the design. No further numbered implementation phase is introduced.

## Scope

This is a loopback operator application with token-authenticated mutations and local
AI enabled. Public publication is not authorized. Offline training, held-out
benchmarks and controlled fault capture remain explicit research workflows. No
frozen research/model rewrite, private-data publication or GitHub push is involved.
