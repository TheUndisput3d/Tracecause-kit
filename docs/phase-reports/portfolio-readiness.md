# Portfolio readiness audit — 2026-10-19

Status: PASS for the local portfolio implementation (AI review). Owner requested a codebase review and completion of portfolio
gaps. Starting checkpoint: eca344b, clean working tree, full Docker stack healthy.

## Plan and boundaries

1. Expose the existing heterogeneous knowledge graph through a bounded, time-scoped
   evidence explorer with service, observation, document and excerpt nodes. Every
   evidence relationship opens its authoritative source; validate document mentions.
2. Add comparison of observed dependencies at two explicit cutoffs, preserving each
   source's time and distinguishing absence in a lookback from proof of removal.
3. Bound timeline rendering across sparse long intervals; improve loading of heavy
   visualizations. Keep accessible graph/table and citation inspection available.
4. Update stale demo/case-study/architecture materials and capture actual screenshots.
5. Run current regression, real database/API/browser, actual local AI and Docker
   verification. Preserve original research, models, snapshots and failed attempts.

No public deployment or GitHub push is authorized. New infrastructure/deployment
entities without source data and causal claims remain outside the portfolio scope.
Research limitations are distinct from incomplete application behavior.

## Implemented changes

- Added source verification of projected MENTIONS and typed assertion nodes,
  exact quotes/offsets, subject and complete excerpt support. Runbook checks in
  investigations now require a verified SUGGESTS_CHECK subject (template v8).
- Added bounded, directed three-hop authored-check joins with continuous cited
  paths; cycle handling, deterministic shortest paths and explicit truncation.
- Added heterogeneous KG API/UI with source inspection, accessible tables,
  focused neighborhoods and zoom controls. Temporal comparison pins source scope.
- Timeline gaps now allocate one missing sentinel per gap, rather than every
  ten-second point across an unbounded interval.
- Added controlled KG competency evaluator and regression tests, safe dependency
  exception-type logging, isolated browser artifact output, updated demo/case study,
  interview guide, API guide and ADR 0016. No migration or model retraining required.

## Measured evidence

All outputs are under `reports/portfolio-20261019`; earlier failed attempts remain.

| Gate | Actual result |
|---|---|
| `make check` | PASS: 288 Python tests, 7 Vitest tests, Ruff/mypy/contracts/types/lint/build |
| `pytest tests/integration -q` | PASS: 44 real PostgreSQL/Neo4j/API tests in 147.38s |
| `pytest tests/model/test_minilm.py -q` | PASS: actual local embedding model, 1 test |
| Controlled KG competency | PASS: 8/8 declared topologies; 21/21 checks vs 8/21 direct lookup; zero extras |
| Browser suite | PASS: 16 cases, including actual KG paths, sources, temporal pin, mobile and axe |
| Security scans | PASS: Python/npm advisories and history/working-tree secrets, four exit 0 |
| Docker build/up | PASS: actual backend/web build and full startup |
| Captured v4 + actual Qwen | PASS after one transient dependency retry: catalog first, 6 citations, no fallback; 1545 input/18 output tokens; generation 32.088s, pipeline-before-publication 91.813s |
| Restart/source/UI final closure | PASS: eight active snapshot pointers, 16 saved responses and credentials preserved; 83 Python source files and 15 frontend assets match the reviewed build |

The captured run used predeclared **development** case catalog-01. This is an
operational smoke, not a new held-out ranking score. Its initial retry reported
`dependency_unavailable`; previous logging did not retain the exception type, so
no exact cause is claimed. Added type-only logging preserves diagnostics without
printing credential-bearing exception messages. Frozen v3/v4 models and original
research outputs were not modified.

## Failed attempts and corrections

The first new integration fixture chose check paragraphs without explicit,
unambiguous service subjects; exclusion was correct. It now imports source checks
that actually have such subjects. The next test expected 422 for graph corruption;
the existing API intentionally returns sanitized 409 resource_conflict. The test
now asserts that established contract, while verifying corruption is rejected and
original source restoration succeeds.

The first expanded-mobile browser assertion ran before the chart ResizeObserver
settled. Inspection at 360px showed no remaining overflow; the assertion now waits
for the real layout condition. The first standalone Docker accessibility harness
used browser.newPage; axe requires an explicit newContext. Its failed artifact is
retained and the harness corrected. Screenshots prompted more legible focused graph
spacing and zoom controls; the final browser gate rechecks those changes.

## Interpretation and remaining limits

This is a working local KG/AI capstone implementation, with explicit source and
query semantics. The eight topology cases are synthetic query competencies, not
production topology coverage or evidence of diagnosis effectiveness. MiniShop
captures and authored demo documents remain narrow; no independent human/operational
study or new held-out accuracy claim exists. Two frontend bundles still trigger
size warnings. No GitHub push or public deployment is authorized/performed.

## Final closure and reproducibility

Final actual Docker browser: `docker-ollama/report.json` and `result-summary.json`;
v8, real Qwen, no fallback, 1287 input/19 output tokens, 29.124s generation and
34.601s pipeline-before-publication. A single run is not a latency SLO. The separate
`docker-knowledge-final/report.json` records real source/path inspection, preserved
temporal pin, 360/1440px widths, zero axe violations and zero browser errors.
Actual screenshots are copied into docs/assets; they are not design mockups.

Commands executed from the repository root (pinned uv/Node on PATH, UV_NO_SYNC=1):

```sh
make check
.venv/bin/pytest tests/integration -q
.venv/bin/pytest tests/model/test_minilm.py -q
.venv/bin/python -m scripts.evaluation.knowledge_competency --output NEW.json
.venv/bin/python -m scripts.ci.browser --output NEW_BROWSER_DIRECTORY
.venv/bin/python -m scripts.ci.scans --output NEW_SCAN_DIRECTORY
docker compose -f compose.full.yaml build api web
docker compose -f compose.full.yaml up -d --wait --wait-timeout 2400
.venv/bin/python -m scripts.deployment.transfer_smoke --output NEW_CAPTURE_DIRECTORY
.venv/bin/python -m scripts.deployment.full_lifecycle --output NEW_RESTART_DIRECTORY
.tools/node_modules/node/bin/node scripts/deployment/knowledge_browser.mjs NEW_KG_DIRECTORY
.tools/node_modules/node/bin/node scripts/deployment/full_browser.mjs NEW_LLM_DIRECTORY http://127.0.0.1:14000 ollama
.venv/bin/python -m scripts.deployment.full_source_audit --output NEW_SOURCE_AUDIT.json
```

`NEW_*` denotes a fresh artifact path; actual paths are recorded below. Commands
all completed with exit 0 in their successful final runs. Captured smoke requires
the retained local capture/review artifacts; ordinary Docker startup does not.

- `check-release.log`: final lint, type, contract, 288 Python/7 frontend tests/build.
- `integration.log`: 44 real database/API tests; `knowledge-api-final.log` repeats
  the new source/path/corruption gate against final Python code (1 PASS, 27.18s).
- `browser-release/results.json`: final 16-case suite; boundary simulations remain
  explicitly labeled and are not counted as real backend evidence.
- `knowledge-competency-final.json`: declared cases, results, proof paths and hashes;
  committed copy in `docs/assets/portfolio-knowledge-competency.json`.
- `scans-final/summary.json`: four scanners exit 0.
- `lifecycle/report.json`: upload, full stop/build/up, source-state preservation and
  six healthy runtime services/four successful startup jobs. A subsequent web-only
  rebuild includes final visualization spacing and was independently browser checked.
- `source-equivalence-final.json`: final running API/worker source and served frontend
  byte equivalence, model hashes and container boundaries.

Historical fresh-environment setup and restore/rollback checks remain documented
in the phase19/release reports. This follow-up rebuilt production images with
pinned dependencies and exercised the new paths; it did not claim a new production
benchmark, independent human review or a new backup restore experiment.

Verdict: the locally runnable project is suitable to present as an **AI-assisted
KG engineering capstone**, with the disclosed controlled-data/research limitations.
All work remains local. No GitHub push or public deployment was performed.
