# Release audit — through 21 October 2026

Reviewer: Codex, the implementation agent, acting under the owner's explicit review
delegation (ADR0010). Review origin is **AI**, not independent human adjudication.
**Local release verdict: PASS**, with the documented scientific and operational
limits. The clean-checkout gate, restored-source audit and final diagnostics passed.
Public hosting is **BLOCKED: not authorized**; no GitHub push was performed.

## Acceptance review

| Requirement | Gate | Source and executed evidence |
|---|---|---|
| R01 Locked foundation | PASS | uv/npm locks, Docker digest pins; phase00 doctor and DB connections; phase19 fresh lockfile installation and actual Python/Node builds. |
| R02 Config/contracts | PASS | contracts/config modules, schema exporter, generated frontend validators; invalid-input/property tests and schema/type parity in release-check. |
| R03 Fixtures/splits | PASS | scripts/fixtures, private/public validators; repeat seed hashes, 120 episodes with 70/25/25 split, group isolation and runtime gold exclusion. |
| R04 OTLP normalization | PASS | ingestion parser/redaction; malformed, duplicate, late, integer-ns and offset tests. Final audit adds normalized-file pointer checks. |
| R05 Persistence/jobs | PASS | storage/jobs modules; real PostgreSQL tests, immutable artifact hashes, lease/idempotency/failure tests and phase16 crash drill. |
| R06 Signals | PASS | telemetry signals/persistence; role separation, quantile, baseline/missingness tests; phase19 independently recomputed six-request payment bucket. |
| R07 Temporal graph | PASS | graph snapshot/query/recovery modules; actual Neo4j tests, cutoff/scope/publication adversaries, frozen edge audit and phase19 source parent pairs. Edges do not establish causality. |
| R08 Authored sources | PASS | documents modules; exact character offset round trips, alias ambiguity/quarantine tests and actual citation inspection. |
| R09 Retrieval | PASS | retrieval engine/embeddings; actual pinned MiniLM, eligible-only fair corpus/context and injection/scope/time tests. Frozen relevance judgments remain incomplete. |
| R10 Deterministic investigation | PASS | investigation service/template/validation and CLI; early/later smoke, healthy/insufficient states, every citation opened and byte-seek tamper regressions. |
| R11 Local generation | PASS | bounded Ollama adapter/fence; real pinned0.40.2/Qwen phase18 output, source/schema validation, invalid-output tests and actual phase16 crash recovery. Latest offset repair is independently versioned. |
| R12 Trained scorer | PASS | ranking training/features/model; grouped fitting, exported inference parity and frozen model SHA; negative captured transfer retained. Fresh training reproduction is recorded below. |
| R13 Captured telemetry | PASS | capture runner/audit/review; all24 real MiniShop captures, cleanup, hashed bundles, actual parent pairs and explicit AI source review (phase12). |
| R14 Evaluation | PASS | frozen phase13 synthetic/captured benchmark, baselines/ablations, candidate/exposed/coverage denominators, claim-review artifacts and generated EVALUATION_RESULTS. Undefined captured test ranking is explicit. |
| R15 Versioned API | PASS | API/resources/reads/jobs; actual authenticated HTTP import-to-investigation workflow, paging, series, evidence, idempotency and safe failure tests. |
| R16 Frontend | PASS | actual React dashboard; 15 browser cases (seven real workflows, eight explicit UI simulations), mobile/keyboard/axe evidence and phase19 real container browser screenshots. |
| R17 Safety/recovery | PASS | source validation, bounded operations, durable generation fence, snapshot membership; actual phase16 crashes and phase19 new SQL/graph/artifact restore. |
| R18 CI/performance | PASS | pinned workflows and locally executed release-check; phase17 measured100k import/equality, eight measured budgets, actual queued HTTP jobs and scanners. Remote GitHub Actions execution is not claimed. |
| R19 Deployment | PASS locally | phase18 fresh container volumes, real local TLS/model/rollback/restart; phase19 source-archived immutable upgrade, public permissions, nonroot image audit and restored citation replay. External hosting remains BLOCKED. |
| R20 Portfolio/final audit | PASS | README/screenshots, cards, ADRs, demo, case study/interview/future work; clean release-check and training reproduction passed. |

## Defects found and repaired during final review

1. CLI learned default contradicted the captured transfer decision. CLI/API/UI now
   default to heuristic. The frozen ranker and comparisons were not retuned.
2. Legacy observation locators paired a normalized-artifact hash with an original
   OTLP offset. Template v7 computes/validates the true normalized byte position,
   scope, span identity and cutoff. Earlier metadata-only checks missed the defect.
   Saved reports retain original bytes and the UI marks them; opened evidence
   resolves current locations. See ADR0013 and the independent byte-seek tests.
3. A fresh checkout exposed missing fixture generation before API integration
   tests:13 failed/29 passed. `test-integration` now declares the `fixtures`
   prerequisite. The failed log remains `reports/phase19-clean-v1/release-check.log`;
   the corrective source manifest and rerun are separate.

## Independent source and recovery inspection

`reports/phase19-release/source-audit-with-lineage.json` records20 actual citation
opens across four saved reports. Fourteen historical citations required a repaired
locator; all six v7 locators matched saved metadata. For the payment observation,
independent normalized-source calculations produced6requests,3 HTTP5xx,p50=17.5 ms,
p95=23.75 ms. Six checkout CLIENT→payment SERVER parent/span identities were found.
The original OTLP hash and record/trace/span identity also matched. This checks a
representative synthetic record; it is not causal or held-out accuracy evidence.

The captured claim review was re-inspected, including unsupported claims about
zero-error payment/catalog buckets without an abnormal baseline. Original decisions
remain69supported/13 partial/8 unsupported across90structured claims. No independent
human review or new evaluation is implied. The frozen ranker artifact hash remains
`a824ecaef074ec37402b0caa7bf880c92929e57171b973ff2e0e9e72feec277f`.

Phase19 restore verified33artifacts, schema0007 and two rebuilt snapshots in a new
SQL database/empty graph. Four saved results/20 citations reopened; a new six-citation
v7 result was computed without replay. Evidence: `restore-v1.json` and
`restore-replay-v1.json` under `reports/phase19-release`. Isolated graph stopped;
volumes/database/evidence retained.

## Source, permissions and presentation review

Runtime/source scan found no TODO/FIXME/NotImplemented placeholders. Four `pass`
statements were classified: SQLAlchemy declarative base, bounded health retry,
and two license-metadata fallback attempts. None fabricates runtime success.
Test providers remain in tests. Public release has no worker/model enabled, denies
mutations even with the operator token, blocks private endpoints and exposes only
loopback web ports. Actual image/asset inspection verified UID10001, Python3.12.15,
no baked gold/model cache/secrets and zero secret matches across14frontend assets.

README screenshots come from the actual local container browser, not a mock.
Result tables are regenerated from retained frozen reports without inference.
Local Markdown targets were checked. Dependency inventories preserve package
licenses; external redistribution remains separately subject to their terms.

## Reproduction and limits

The clean local checkout excludes ignored reports, secrets, fixtures and installed
dependencies. Python3.12.13/npm installs, credentials and database volumes are new;
host package/model/browser download caches are reused explicitly. This is not a
no-network installation or a brand-new host. See phase19 report for final command
results and source manifests.

No production incident validation, independent human labels, public TLS/exposure
assessment, causal proof or service-level latency guarantee is claimed. The actual
four-CPU local model response took173.4s in phase18, close to its180s deadline.
Large frontend chunks, event-time-only capture and uncertain-provider fail-closed
recovery remain documented limits. Source validity does not prove semantic support.

Fresh reproduction completed:243 Python tests,4 frontend unit tests,42 real database/API
tests,1 actual MiniLM test,15 browser checks and four scans passed. Training and
development reporting exited0; only Python-version metadata differs from the
frozen artifact. Coefficients, preprocessing, rejection threshold, split hashes and
development decisions are identical. The frozen artifact remains unchanged.

Final root recheck:245 Python/4 frontend unit tests and full lint/type/schema/build
checks passed. The phase19 performance rerun passed all eight tolerances. Actual
native-model browser verification recorded one rejected-output fallback followed
by a same-request diagnostic success and one fresh-request browser success. This
variability remains a documented limitation; no validator was weakened.

## Operational KG capstone release — 21 October 2026

The owner broadened the final scope to include source-backed operational and
historical graph reasoning, three varied captured applications, a substantially
redesigned UI, live media, a comprehensive README and automatic full-stack Docker
startup. The implementation is complete for that local scope. The six captured
episodes contain 28,935 spans, 129 operational receipts and 900 directly linked
producer/consumer pairs. Reference labels stay outside the runtime image.

Final code and runtime checks passed: 406 Python unit/contract tests, 11 frontend
unit tests, 51 real PostgreSQL/Neo4j/API integration tests, four advisory/secret
scans, three live browser recorders, source equivalence for 94 Python and 17 web
files, six healthy runtime services and four successful initialization jobs.
An isolated fresh-state restore rebuilt seven snapshots from 97 verified artifacts
and reopened 195 scoped proofs before and after; the final-image lifecycle drill
preserved the operator credential, 15 active snapshots and 21 saved results.

The frozen operational comparison completed 12 replay queries and opened 1,021
citations, with zero future events. First-place reference event identity was 6/6
for graph + temporal, 2/6 without graph, and 6/6 without time. It therefore
supports a controlled graph contribution in these scenarios but no measured
incremental ranking lift from the timing condition. The cases are repeated local
experiments, not independent production validation. Failed intermediate runs and
their source-lookup repairs remain documented. See
[the capstone phase report](phase-reports/operational-capstone.md),
[full evaluation result](evaluation/operational/results.json) and
[live media](assets/operational/manifest.json).

The reviewer remains the implementation agent. This is AI-assisted review, not
independent human adjudication. No GitHub push or public hosting occurred.
