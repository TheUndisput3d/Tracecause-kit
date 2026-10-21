# Ranker transfer and local generation reliability

Requested 2026-10-12 after the complete Docker deployment. Status: PASS (local).

## Plan and evaluation boundaries

1. Preserve candidate-logistic-v3 and every frozen phase13 report. Train a separately
   versioned candidate on synthetic training groups plus reviewed captured training
   runs. Keep development and test out of fitting, and all repeats of a captured
   fault family together in group cross-validation. Use only training/development
   data for the new comparison; it is a development experiment, not a new untouched
   benchmark. Document the remaining small-data/generalization limits.
2. Replace full-report LLM copying with a bounded evidence-plan interface. A local
   model chooses a question-relevant focus and references to server-owned facts.
   A schema constrains those choices; deterministic code fills in values, citations,
   limitations, paths and fixed service ranks. Reject invalid plans before rendering.
   Keep source validation and transport/time/concurrency limits intact.
3. Retain the previous provider for paired offline diagnostic measurements only.
   Measure first-attempt validity, attempts, output tokens, latency and focus selection
   with actual pinned Qwen calls, including injection, insufficient evidence and
   dependencies. Do not present deterministic rendering as free-form model authorship.
4. Verify regression, actual API/browser behavior and Docker rollout. Record failures
   as well as successful runs. Never push GitHub without permission.

Evidence directory: `reports/reliability-v1/` (local retained diagnostic artifacts).

## Implemented behavior

- `investigation/planning.py`, `prompts/runtime/plan_v1.txt`, production provider and
  independent validator implement schema-constrained evidence selection and exact
  compilation. The UI states that AI selects facts and code renders them. No raw
  log/document text, tool access or model-authored factual fields enter this plan.
- `ranking/{features,model,table,train}.py` support separately versioned v4 and
  preserve legacy v3. CLI and Docker registration select the new bundled artifact;
  heuristic remains the application default. Training is explicit and offline.
- `scripts/evaluation/train_transfer.py` checks retained prepared-data hashes and
  the validated AI review sheet, restricts training/selection to train/dev, groups
  captured repeats together and records comparisons by data origin.
- `scripts/evaluation/generation_reliability.py` records actual local model outputs,
  timings, token counts, retries, validation and expected-focus checks. Legacy
  full-report generation exists only as an explicit diagnostic comparison.
- API imports now materialize populated intervening observation buckets. Sparse
  long intervals do not allocate every time bucket, and publication is bounded to
  19,000 observations. Baseline/analysis features and existing observation bytes
  remain unchanged. Historical saved snapshots are not modified.

## Ranker experiment and decision

Actual command: `.venv/bin/python -m scripts.evaluation.train_transfer --output
reports/reliability-v1/ranker-v4-final` (exit0). Reproduction shortcut:
`UV_NO_SYNC=1 make train-transfer TRANSFER_OUTPUT_ROOT=reports/new-transfer-run`.
Requires ML dependencies and retained captured prepared/review bundles. Clean
Docker inference uses the bundled artifact and does not require these private
training files. No evaluation gold or intervention controls enter runtime images.

Fitted 50 identifiable episodes/270 candidate rows:40 synthetic plus ten captured
training runs across two intervention families. Five grouped training CV folds;
imputation/scaling fit within each fold. Four graph-relative features supplement
the existing eleven. C=10 was selected on the minimum development-origin Hit@1,
then mean-origin MRR, train CV MRR and smaller C. Rejection is development-calibrated
per origin. Development consists of25 synthetic and six captured episodes.

| Development measurement | Frozen v3 | V4 | Heuristic |
|---|---:|---:|---:|
| Captured candidate Hit@1 (identifiable) | 0/5 | 5/5 | 1/5 |
| Synthetic candidate Hit@1 (identifiable) | 10/10 | 8/10 | 4/10 |
| Captured threshold answered/correct | 0/6, 0 | 5/6, 5 | n/a |
| Synthetic threshold answered/correct | 5/25, 5 | 5/25, 5 | n/a |

V4's development threshold checks had zero false alarms in these small sets.
Captured threshold2.6296358973918252; synthetic/global3.61701932997257. Candidate
accuracy and answered accuracy are separate. Repeated captured development runs
all share the catalog-error family. Development was used to explore/select
features and settings, so **none of these numbers is a held-out test claim**.
Heuristic stays the application default because transfer improves with a synthetic
regression and small, AI-reviewed data. There was no new test-set evaluation.

New tracked artifact SHA256:
`76b66e66d9c5686eecced3cd82751977f91b961740a3f5521ffe3448cda26811`.
Frozen v3 SHA256 remains
`a824ecaef074ec37402b0caa7bf880c92929e57171b973ff2e0e9e72feec277f`;
`legacy-training-reproduction/` refitted it byte-for-byte with Python3.12.3.

Retained unsuccessful alternatives: initial healthy-training group boundary failure
(`ranker-v4.log`); mixed-data original features still0/5captured (`ranker-v4/`);
train-CV-selected expanded features4/5captured but5/10synthetic
(`ranker-v4-features/`). The regularization, pairwise and threshold diagnostic JSONs
record explored alternatives. Those runs were not promoted. Compact provenance
replaced an overlong explanation-reference list before the final artifact export.

## Actual local Qwen diagnostics

Pinned Ollama0.40.2, `qwen3:4b-instruct-2507-q4_K_M`, digest
`sha256:0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`.
Requests used the actual Docker provider with the same slot/deadline and source
template; new output directories preserve each run. Source is
`generation-source.json`, derived from a real saved graph investigation. Paired
comparison uses one supported hypothesis for both protocols. Seven cases cover
overview, measurements, dependency, certainty, injected instructions, insufficient
evidence and healthy evidence. Final variations add four new question wordings.

| Protocol / artifact | Calls | First-attempt valid | Expected focus | Mean output tokens | Total seconds |
|---|---:|---:|---:|---:|---:|
| Historical full narrative, `generation/legacy-v1` | 7 | 7 | n/a | 521.86 | 544.54 |
| Initial plan, `generation/plan-v1` | 21 | 21 | 15/21 | 12.71 | 41.26 |
| Revised plan, `generation/plan-v2` | 21 | 21 | 21/21 | 13.29 | 59.33 |
| New wording checks, `generation/variations-v1` | 12 | 12 | 12/12 | 14.50 | 32.31 |

The initial plan was factually valid but selected the wrong focus in six calls.
Added few-shot intent examples and bounded request-policy routing, then reran all
seven cases three times plus four question variations three times. The final33
calls required no repair or template fallback. The runner now exits nonzero on
invalid output or incorrect plan focus. Initial failure evidence remains retained.

The historical comparison also passed every call: this does **not** demonstrate
an empirical reduction in failure probability. Structural improvement is that
successful plan generation cannot author factual values/support/ranks. Output
tokens dropped substantially. Timing is diagnostic only: builds/regression work
overlapped parts of the measurements and the cases are repeated, not independent
production samples. Model availability and correct question understanding remain
limitations. The new summary is deliberately bounded server-authored prose.

Actual runner inside a one-off existing Docker worker image (read-only source and
prompt mounts, retained writable diagnostic output) used:

```sh
python /verify/generation_reliability.py \
  --source /verify/reports/generation-source.json \
  --output /verify/reports/generation/plan-v2 \
  --prompt-root /verify --protocol plan --repeats 3
```

The legacy comparison used `--protocol legacy --repeats 1`; variations added
`--variations`. Each final run records source hash, pinned digest, prompt hash and
questions in its manifest. No cloud model or paid API was used.

## Deployed evidence and discovered repair

Initial Docker API smoke passed three real queued investigations. Its fresh Qwen
case completed in5.741s including queue/polling, with1290input/19output tokens,
seven citations and no fallback (`smoke/report.json`). Actual Chromium operator
login, new AI job, authorship notice and citation inspection passed with no browser
errors/external requests (`browser/report.json`, three actual screenshots).

The first captured development upload selected v4 but returned insufficient
evidence: `candidate_support_unavailable_in_snapshot` (`captured-api/`). Diagnosis
found only baseline/final-window observations in the imported snapshot; all five
services lacked support at the requested earlier cutoff. This was a genuine failed
deployment check, not an LLM invalid-answer failure. Preserved its snapshot/report.

After retaining intermediate buckets, `.venv/bin/python -m
scripts.deployment.transfer_smoke --output reports/reliability-v1/captured-api-v2`
passed with the same predeclared `catalog-01` development case and same cutoff.
An actual upload produced a new immutable snapshot. GRAPH retrieval + learned v4 +
local Qwen ranked `catalog` first, returned supported hypotheses, and resolved all
six citations through the API. No fallback;1555input/18output tokens. Pipeline
85.099s, generation24.624s under concurrent developer work. This is one development
case proving deployed integration, not another ranking benchmark.

The new earlier-cutoff API regression passed against real PostgreSQL/Neo4j.
Unit checks preserve original feature/observation identities, reject excessive
history size and handle sparse long time ranges without huge allocation.

## Remaining limits and next work

The architecture makes factual output deterministic once evidence is selected;
it cannot guarantee truth beyond the evidence, model uptime, correct focus or a
causal diagnosis. Fallback remains appropriate for actual provider/validation
failure. More independently reviewed captured families, prospective evaluation
and production utility studies remain research work. Public hosting and GitHub
push are not authorized. No next numbered phase is opened by this follow-up.

Decision: [ADR0015](../adr/0015-evidence-plans-and-transfer-ranker.md).

## Verification gates

All commands ran from the repository root. Developer checks used the pinned Node
binary in `.tools/node_modules/node/bin`, the existing Python3.12.3 ML environment
and `UV_NO_SYNC=1` so uv did not remove already-installed ML dependencies.

| Gate | Result | Command / retained evidence |
|---|---|---|
| Lint, types, contracts, unit, frontend build | PASS, exit0 | `UV_NO_SYNC=1 make check`; `check-reviewed.log`:269 Python tests,4 Vitest tests; mypy82 source files |
| Real database/API regression | PASS, exit0 | `UV_NO_SYNC=1 make test-integration`; `integration-complete.log`:43 tests,106.96s |
| Final focused architecture/history/model regressions | PASS, exit0 | `pytest tests/unit/test_ranker_transfer.py tests/unit/test_generation_plan.py tests/unit/test_signals.py -q`; `review-unit.log`:33 tests |
| Real local generation | PASS, exit0 | `generation/plan-v2`, `generation/variations-v1`;33/33 valid first attempts and expected focuses |
| Actual Docker API/captured/browser | PASS | `smoke/report.json`, `captured-api-final/report.json`, `browser-final/report.json` |
| Docker build and start | PASS, exit0 | `docker compose -f compose.full.yaml build api`; `build-reviewed.log`; actual lifecycle restart below rebuilt all services |
| Stop/up persistence and runtime boundaries | PASS, exit0 | `.venv/bin/python -m scripts.deployment.full_lifecycle --output reports/reliability-v1/lifecycle`; five snapshots/12 saved responses and credentials unchanged |
| Dependency and secret scans | PASS, exit0 | `.venv/bin/python -m scripts.ci.scans --output reports/reliability-v1/scans-reviewed`; four scanner exit codes0 |
| Frozen model preservation | PASS | v3 refit/hash above; no historical benchmark or model bytes changed |

Six runtime services are healthy; four startup jobs exited0. API/worker retain
UID10001, read-only root, no admin secret volume; API artifacts remain read-only.
Only web is published on loopback14000. Private training labels and review
artifacts are not runtime mounts. Existing development and read-only release
projects remain separate. Frontend bundle-size and dependency deprecation warnings
remain; no test was skipped to treat a missing integration as passed.

After the final build/restart, repeated the captured upload and v4/Qwen request in
`captured-api-final/` (exit0). `catalog` again ranked first, supported hypotheses
and all six API citations passed, with no fallback. Actual1533input/18output tokens,
62.620s pipeline and24.203s generation. New snapshot/report IDs are retained; the
failed first attempt was not overwritten. This repeat verifies the deployed
bounded-history implementation, not an independent data point for model quality.

Final actual Chromium rerun also passed (exit0):
`.tools/node_modules/node/bin/node scripts/deployment/full_browser.mjs
reports/reliability-v1/browser-final http://127.0.0.1:14000 ollama`.
It submitted a fresh AI investigation, checked the authorship notice, opened a
summary citation and recorded three screenshots; no JavaScript errors, external
requests or model fallback. Verification report and final source manifest are
retained beside the earlier runs.
