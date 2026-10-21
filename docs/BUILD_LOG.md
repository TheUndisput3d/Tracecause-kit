# Build log

Current checkpoint: phases00–19 and the Docker/reliability follow-ups are verified
locally. See the latest dated entries and IMPLEMENTATION_STATUS. The opening
paragraphs below describe an earlier checkpoint and are retained as history.

Kit status: Phases 00–11 implemented and verified through 11 October 2026. Phase 12 is in progress. Durable redacted trace ingestion, deterministic signal analysis, a temporal Neo4j projection, authored document ingestion, comparable retrieval, cited template investigation, optional local Qwen generation and a trained candidate ranker are available.

A short real Collector capture and four full declared MiniShop episodes exist locally; the 24-episode protocol remains partial. A provisional simulator-labeled synthetic test ranking diagnostic was run in Phase 13 groundwork, but the independent review and full five-system benchmark are pending. No completed captured benchmark, screenshot or deployment is claimed by this file. Static kit validation is recorded separately in KIT_VALIDATION.md.

For each phase, record date, phase number, actual changed files, exact commands/results, evidence paths, PASS/FAIL/BLOCKED gates, decisions and remaining issues. Use `docs/phase-reports/NN.md`. Keep measurements distinct from targets.

## 2026-10-10 — Phase 00: PASS

Created the Python/FastAPI CLI/API foundation, React/TypeScript frontend, locked dependencies, minimal PostgreSQL/Neo4j Compose stack, non-root application images, secret generation, checks, version/license records, and ADR. `make bootstrap`, `make check` (3 unit tests), `make db-check`, `make build`, `docker compose config --quiet`, fresh PostgreSQL init probe, local CPU PyTorch import probe, and container HTTP probes succeeded. See `docs/phase-reports/00.md` for exact results and earlier failures repaired during implementation. No ingestion, graph projection, model inference, benchmark, capture, or complete application deployment is claimed. Next: Phase 01 contracts and configuration.

## 2026-10-10 — Phase 01: PASS

Phase 00 evidence reviewed. Added typed domain/public models, strict validated YAML/environment settings, canonical source/semantic hashes, boundary protocols, 24 generated JSON Schemas, OpenAPI components and TypeScript types. `make bootstrap`, `make check` (23 Python tests and one Vitest test), `make db-check`, `make build`, container API import, `docker compose config --quiet`, and `uv lock --check` passed. Tests cover supplied examples, invalid IDs/enums/limits/cutoffs, hidden fields, local-mode opt-in, scope/citation membership and nanosecond JSON precision. See `docs/phase-reports/01.md` for exact evidence and the local-pricing specification conflict resolved in ADR 0002. No domain ingestion or full API is claimed. Next: Phase 02 synthetic fixtures/evaluation protocol.

## 2026-10-10 — Phase 02: PASS

Phase 01 evidence reviewed. Added a deterministic OTLP fixture generator, a one-episode committed smoke fixture, an ignored 120-episode public dataset and separate ignored evaluator truth. Froze 24 group assignments, 70/25/25 episodes, healthy allocation and held-out topology C. Added import file allowlisting, split/hash/ID validation, evaluation protocol, data card and draft annotation examples requiring human review. `make fixtures-smoke`, `make fixtures`, `make check` (36 Python tests and one Vitest), `make db-check`, `uv lock --check`, `docker compose config --quiet`, `make build`, backend container import and metadata scans passed. See `docs/phase-reports/02.md` for artifact hashes and limits. No captured telemetry or measured system accuracy is claimed. Next: Phase 03 OTLP ingestion.

## 2026-10-10 — Phase 03: PASS

Added bounded streaming OTLP JSON/NDJSON parsing, exact source locators, scoped service identity and versioned aliases, allowlist redaction, deterministic duplicate quarantine, and a strict/partial normalization CLI. The pinned official OTLP JSON fixture parsed successfully. `make check` passed with 67 Python tests and one Vitest test; `make fixtures-smoke`, `make fixtures`, real `make db-check`, `uv lock --check`, `docker compose config --quiet`, `make build`, backend-container CLI probe and `make ingestion-sweep` over 120 episodes passed. The smoke CLI emitted 71 accepted spans and one duplicate, with no rejection. See `docs/phase-reports/03.md` for exact commands, hashes, adversarial cases and limits. Collector file encoding remains explicitly unsupported until a pinned captured conformance fixture exists. Next: Phase 04 persistence and durable jobs.

## 2026-10-10 — Phase 04: PASS

Added authoritative PostgreSQL tables and repeatable Alembic migrations, a SHA-256 artifact repository, scoped idempotent imports and a lease worker with checkpoints, recovery, retries and cancellation. `make check` passed (70 Python unit/contract tests, one Vitest and frontend build); `make test-integration` passed 18 real PostgreSQL tests; `make db-check`, `make migrate`, Alembic schema check, `uv lock --check`, `docker compose config --quiet` and `make build` passed. A fresh container import and separate worker processed 534 spans into an `IMPORTED` episode with no active snapshot; container reconciliation was clean. See `docs/phase-reports/04.md` for exact evidence, limitations and files. Next: Phase 05 trace-derived signals.

## 2026-10-11 — Phase 05: PASS

Added deterministic 10-second observations, cutoff-safe 60-second signal windows, parent-backed dependency evidence, robust deviation features, exact PostgreSQL span references and immutable report artifacts. `make check` passed (81 Python unit/contract tests, one Vitest and frontend build); `make test-integration` passed 19 tests against real PostgreSQL; `make db-check`, `uv lock --check`, `docker compose config --quiet` and `make build` passed. In a container, a full synthetic episode produced 333 observations and five signals; repeated materialization returned the same hashes, inspection resolved 320 eligible source locators, and reconciliation was clean. See `docs/phase-reports/05.md` for exact hashes, commands and limitations. Next: Phase 06 temporal graph.

## 2026-10-11 — Phase 06: PASS

Added snapshot-local Neo4j projection, exact relationship validation, PostgreSQL READY publication, bounded scope/time reads, graph reconciliation and reader-lease-aware retirement. `make check` passed 81 Python unit/contract tests, one Vitest and frontend build; `make test-integration` passed 21 real PostgreSQL tests, including two real Neo4j graph cases. `make migrate`, pinned `make db-check`, `uv lock --check`, `docker compose config --quiet` and `make build` passed. A global uv 0.13.0 version mismatch was resolved for checks by placing cached pinned uv 0.11.19 first in `PATH`; no global installation was changed. See `docs/phase-reports/06.md` for exact scope, adversarial checks and limits. Next: Phase 07 documents and entity linking.

## 2026-10-11 — Phase 07: PASS

Added 24 authored demonstration runbooks, exact-offset chunking with the pinned MiniLM tokenizer, deterministic scoped entity resolution, quote-verified assertions and temporal document graph projection. `make check` passed 86 Python unit/contract tests, frontend lint/typecheck, one Vitest and Vite build. `make test-integration` passed 22 real PostgreSQL tests including three Neo4j cases. `make migrate`, `make db-check`, `make build`, backend image tokenizer import, `uv lock --check`, `docker compose config --quiet`, offline leakage scan and link review passed. The annotation set is author-reviewed; independent human review remains pending. See `docs/phase-reports/07.md` for exact scope, findings and limitations. Next: Phase 08 retrieval.

## 2026-10-11 — Phase 08: PASS

Implemented exact eligible-corpus dense, lexical, hybrid and graph retrieval with pinned MiniLM, a shared `Retrievable` graph label, provenance-bearing evidence and a no-generation comparison CLI. `make check` passed 93 Python unit/contract tests plus frontend checks; `make test-integration` passed 22 real PostgreSQL/Neo4j tests, including all four strategies with the actual MiniLM model. The separate pinned-model test, `make migrate`, `make db-check`, `make build`, an offline backend-image inference probe, `uv lock --check` and Compose validation passed. Retrieval quality has not been measured; the small relevance fixture is developer-authored and awaits independent review. See `docs/phase-reports/08.md` for exact scope and limits. Next: Phase 09 deterministic investigation.

## 2026-10-11 — Phase 09: PASS

Added a cutoff-safe deterministic investigation service with a bounded, documented heuristic; typed observations, runbook checks, directed support paths, exact citation and metric validation, content-addressed result persistence/replay, and operator seed/run/inspect/export commands. `make check` passed 95 Python unit/contract tests plus frontend checks; `make test-integration` passed 24 real PostgreSQL/Neo4j tests, including two investigation cases. `make seed`, `make demo-cli`, `make eval-smoke` (two cutoffs, every citation reopened), `make migrate`, `make db-check`, `make build`, `uv lock --check` and Compose validation passed. The public healthy fixture's final window can trigger the initial anomaly rule, while a quiet window returns no anomaly; no measured diagnostic accuracy is claimed. See `docs/phase-reports/09.md` for commands, artifacts and limits. Next: Phase 10 structured local generation.

## 2026-10-11 — Phase 10: PASS

Added optional pinned local Qwen3 generation via Ollama `/api/chat`, JSON schema, exact model-digest/capability checks, local tokenizer budgets, one slot/deadline, one format repair, grounded typed validation, deterministic template fallback and replayable cache identity. `make check` passed 102 Python unit/contract tests plus frontend checks; `make test-integration` passed 24 real database tests and `make test-embedding-integration` passed one real MiniLM test. `make models-llm`, `make models-probe`, five actual validated local Qwen investigation cases, cache replay, `make build`, offline backend image probe, `make db-check`, `uv lock --check` and both Compose config variants passed. A disposable `--network none` container using the already installed Ollama 0.18.3 binary and pre-fetched model returned valid schema JSON from the pinned Qwen model. The optional pinned Compose image was not run. Generated prose mostly copies the grounded template; semantic entailment and diagnostic quality remain unmeasured. See `docs/phase-reports/10.md` for exact evidence and limits. Next: Phase 11 trained candidate ranker.

## 2026-10-11 — Phase 11: PASS

Added a train/development-only access manifest, shared cutoff-safe candidate features, five-fold group-aware sklearn fitting, episode-weighted relevance scoring, candidate-recall-aware metrics, development-set model and rejection selection, auditable JSON export, registration and source-linked score contributions. The reference model is committed under `models/candidate-logistic-v3/model.json` and reproduced the same SHA-256 on a second `make train` run. On 10 identifiable synthetic development episodes the learned Hit@1 was 10/10 versus heuristic 4/10; 15 healthy/unidentifiable development episodes were tracked separately, and the rejection threshold answered five of 25. These are small synthetic development results, not test or captured-telemetry claims. `make check` passed 109 Python unit/contract tests plus frontend checks; `make test-integration` passed 24 real database tests. Real supported and abstaining learned investigations, replay, `make eval-smoke`, `make demo-llm`, `make build`, offline backend model load, `make migrate`, `make db-check`, `make test-embedding-integration`, lock and Compose checks passed. See `docs/phase-reports/11.md` and `docs/MODEL_CARD.md` for commands, metrics, hashes and limits. Next: Phase 12 captured telemetry.

## 2026-10-11 — Phase 12: IN PROGRESS

The separate five-role MiniShop stack and pinned Collector exported real OTLP JSONL. The smoke run produced 24 traces/528 spans and a 22-span encoding fixture. Four of the frozen 24 benchmark runs are complete and audited: all payment-error training episodes, each with observable effects and no automatic quality issues. A real SIGTERM during active injection exited 130, reset all controls and stopped the Collector. The initial interactive full runner was interrupted; collection resumed under `tracecause-capture-20261011.service` with an exclusive control lock, verified completed source hashes and atomic progress. The fifth run is collecting; `make capture-status` is authoritative.

Fixed a baseline-contamination bug: real baseline ends must round down to a closed signal bucket, not up into intervention data. Idempotent import repairs old boundaries; new projections exclude obsolete derived signals. Real PostgreSQL rematerialization and Neo4j publication succeeded for smoke and the first full episode. Corrected storefront-symptom template investigation still ranked payment third and the frozen learned model abstained; all five exposed heuristic citations reopened. This is one training-episode diagnostic, not a benchmark. No-symptom requests have different candidate ordering and are recorded separately. Availability replay now refuses episodes without per-record timestamps even for empty windows, and CLI seeding applies the same baseline rule.

`make check` passed 128 unit/contract tests plus frontend checks before the review-packet addition; `make test-integration` passed 25 real database tests after the availability regression. The first baseline integration attempt exposed a queued-job test-isolation failure, which was repaired before rerunning the full suite. `make capture-review-packet` generated actual measurements and source links for the four completed runs. `make capture-review-validate` reports zero reviewed rows and `partial_review`. Remaining collection, representative independent human review and unmeasured exporter-drop counters remain explicit limitations. See `docs/phase-reports/12.md`, `docs/CAPTURE_REVIEW.md` and `docs/CAPTURE_DATA_CARD.md`.

## 2026-10-11 — Phase 13: IN PROGRESS

Pure evaluation metrics, including candidate-miss and conditional denominators, 120 deterministic unreviewed question drafts and a one-shot provisional synthetic test ranking diagnostic exist. With the Phase 11 model frozen, ten simulator-labeled identifiable synthetic test episodes scored learned Hit@1 10/10 and heuristic Hit@1 1/10; 15 healthy/unidentifiable episodes were separated. The question review importer now requires a complete independent attestation, reviewer, approved target set and graded evidence; it correctly refuses the unfilled current sheet. These labels/questions are not independently reviewed. The five-system benchmark, evidence judgments, ablations and completed captured track are NOT RUN. See `docs/phase-reports/13.md`.

Final checkpoint checks: `make check` passed 129 Python tests and all frontend checks; `uv run --locked --group capture pytest tests/capture` passed four tests; capture Compose validation and `git diff --check` passed. The review-packet line-length lint failure was fixed before the successful full rerun.

## 2026-10-11 — Phase 12 continued: IN PROGRESS

The background runner reached eleven completed episodes and started `catalog-02`. Five payment-error and five inventory-delay runs passed the initial refreshed audit, with all intended effects observable; inventory median-delay increases were 799.7–801.4 ms. Hardened offline audit/review validation for frozen protocol checks, source metadata, recomputed quality, exact stage timing and complete test-key coverage. Seventeen capture unit tests and the full `make check` (135 Python tests plus frontend checks) passed. Actual CLI seeding of the first inventory-delay run published READY Neo4j snapshot `318cd4ad-0ab0-4728-a028-51d16d16cad5`. Collection and independent human review remain incomplete. No GitHub push occurred.

The stronger source-recomputed audit completed successfully for eleven benchmark runs (ten train, one dev), covering five payment-error, five inventory-delay and one catalog-error capture. All eleven automatic effects were observable; no quality issues were found. Review coverage remains zero. Audit SHA-256 at this checkpoint: `9f9cd5902273851c156e3b3bee556b4439bb59d1166ba535cc4b577bee1ecf1c`.

The inventory two-cutoff template smoke verification passed against actual PostgreSQL/Neo4j/MiniLM: the intervention cutoff returned `supported_hypotheses`, two paths and five resolved citations; the post-recovery cutoff returned `no_anomaly_detected` and one resolved citation. Artifact: `reports/capture/inventory_investigation_smoke.json`. This verifies source resolution and the observed workflow, not human-confirmed diagnosis accuracy. The background capture remains active; phase acceptance still requires all captures and representative independent human review.


## 2026-10-12 — Autonomous review and evaluation continuation: IN PROGRESS

User-authorized AI review now replaces waiting for human annotations (ADR 0010). Reviewed eleven capture target sets and all 120 synthetic questions; the synthetic resolver reopened 2,834 judgments and 3,668 source references. Prepared all 120 synthetic episodes through isolated real PostgreSQL/Neo4j. Implemented fair five-system offline predictions, shared template narratives/source checks, grouped reports and three canonical ablations. A two-episode smoke produced 26 runs; full `make check` passed 144 tests plus frontend checks. Six new resolver adversarial tests passed separately after a fixture-type repair.

The previous processes stopped with 13 completed captures and a partial benchmark. Resumed `catalog-04` with a new UUID under `tracecause-capture-20261012.service`, preserving incomplete raw data and new source/image provenance. Benchmark resume failed on 74 empty output files left by interruption; preserved those files with hashes and restarted unchanged settings under `tracecause-evaluation-benchmark-recovery-20261012.service`. No completed benchmark score is claimed yet. Remaining capture still follows the frozen 300/300/120-second protocol. No GitHub push or phase-completion commit occurred.


## 2026-10-12 — Synthetic evaluation checkpoint complete; phases 12/13 still IN PROGRESS

Completed 120 synthetic episodes and 1,560 template comparisons. Archived the exact runtime/evaluator source bytes before further edits. Two independent two-episode runs reproduced all 26 semantic outputs. The isolated 25-episode temporal diagnostic exposed zero future items when filtered versus 109 of 300 items when deliberately unrestricted. AI review of 30 outputs from six episodes classified 154/169 cited claims as supported, seven partial and eight unsupported; all findings are retained. A twelve-case authored linking diagnostic matched the expected outcomes and is labeled a regression diagnostic.

Generated `docs/EVALUATION_RESULTS.md` from actual artifacts. Hardened checkpoint persistence with file and directory fsync and failed-rename tests. Added public-only captured preparation at a fixed baseline-relative cutoff, a separate five-system captured transfer evaluator, and gates requiring all 24 real prepared/reviewed samples before full evaluation. Training-only smoke is separate. Fourteen captures are complete, thirteen reviewed, catalog05 collecting; completed captures are being prepared through real PostgreSQL/Neo4j.

Latest `make check`: 161 Python unit/contract tests PASS, Ruff/mypy/contracts and frontend lint/typecheck/Vitest/build PASS. Earlier this continuation `make test-integration`: 25 real DB tests PASS. `git diff --check` PASS. Complete captured evaluation and phase acceptance remain pending; no GitHub push or phase-completion commit occurred.


Follow-up checks: all14 available captures are now audited and explicitly AI-reviewed; no automatic quality issues. Public-only preparation completed14 actual bundles. The two-training-episode captured smoke completed10 system predictions; all exposed citations/numeric claims resolved. Full captured evaluation remains gated. Live capture continues under the user systemd unit; no final capture/model release claim.


## 2026-10-12 — Published graph and captured source-pair checks complete for available data

Inspected the actual repository, phase prompts and live runner. Collection advanced to16/24; all16 available full-duration captures now pass refreshed audit, explicit source-backed AI target review and public-only database preparation. Catalog05 was accepted as a catalog investigation case. Timeout01 is deliberately unidentifiable: checkout has537/600 intervention errors with successful downstream SERVER responses; a triage target does not establish unique cause. Collection continues unchanged under `tracecause-capture-20261012.service`.

Implemented the actual database graph evaluator and ten parent-chain regression cases. `python -m scripts.evaluation.graph_projection` exited0:25 explicit synthetic test snapshots,125/125 edge precision and recall against generator topology,1,375 source-resolved observations/parent pairs. Added captured pair packets and review validation; explicitly inspected48 pairs from four episodes/modes across three stages, all supporting directed calls. Healthy mode is still missing. Neither result is a causal or production-accuracy claim. Generated `docs/EVALUATION_RESULTS.md` from these artifacts.

Captured benchmark hardening adds run/machine/prompt/lock metadata, resume counters and memory/elapsed measurements, scope/query/system checks on saved predictions, a same-corpus guard and versioned output roots. The new two-training-episode smoke exited0 with10 predictions in10.54seconds,738,448KiB peak RSS and zero LLM provider calls. Source bytes were archived before later edits. All10 semantic predictions equal the prior smoke (excluding measured timing/cache fields). Frozen model/settings were not retuned.

Verification: final `make check` exited0 with184 Python unit/contract tests, Ruff/mypy/contracts and frontend lint/typecheck/Vitest/build PASS (`reports/phase13-check-20261012-pairs.log`). `make test-integration` exited0 with25 real PostgreSQL/Neo4j tests in36.31seconds (`reports/phase13-integration-20261012-graph.log`). Initial new-test import-order lint failure was corrected before passing reruns. `git diff --check` passed. Phases12/13 remain IN PROGRESS;14–19 remain unstarted. No phase-completion commit or GitHub push occurred.


## 2026-10-12 — Final healthy collection resumed;21 captures reviewed

The previous runner was inactive after21 complete captures, with healthy02 unfinished. Preserved its raw file and confirmed the capture schedule/harness/image identities were unchanged before resuming under `tracecause-capture-final-20261012.service`. The replacement healthy02 uses a new UUID and full original durations. No date/time rewriting or partial-run publication.

Refreshed source audit, AI target review and public-only preparation completed21 bundles (11train/5dev/5test), no automatic quality issues. Timeout02–05 checkout error counts523,536,520,536/600 with successful downstream responses; marked unidentifiable. Healthy01 has no5xx across all services/stages and no accepted target. Explicit parent-pair review now covers60pairs/five modes, all supported as observed calls. No independent human review or causal-accuracy claim.

Added deterministic captured claim packet selection and configurable review roots, structural citation coverage, answer-coverage episode bootstrap intervals and explicit undefined ranking display when no identifiable cases exist. Added a sanitized aggregate exporter using the existing EvaluationReport contract; the synthetic120episode/585metric export is schema-valid, byte-idempotent and local/unpublished. No private episode rows or labels enter it. Full captured export remains gated.

`make check` exited0:202Python tests plus Ruff/mypy/contracts and all frontend checks. Capture SDK tests4PASS. Existing synthetic predictions were postprocessed for21coverage intervals without new inference. Source review and verification are complete for available captures; healthy02–04 and final captured comparison/claim review remain pending.


Healthy02 completed under the resumed runner, advancing to22/24. Source audit/preparation and explicit AI review passed: all five services have zero5xx in600/600/240requests, all four dependency directions1440matched pairs. Absolute caller latency decreases after baseline and no fault target is accepted. Healthy03 is collecting. The actual full evaluator was invoked against22reviewed inputs and correctly refused before creating any prediction files (`phase13-captured-incomplete-gate.json`). Final regression rerun:25real database tests PASS36.27s and real MiniLM test1PASS3.96s.


## 2026-10-12 — Final capture and report continuation

Healthy03 completed; 23/24 captures now pass source audit, explicit AI review and public-only database preparation. Healthy03 has zero HTTP 5xx at every service/stage, four edges with 1,440 pairs each, and no accepted target. Healthy04 is collecting under the unchanged final systemd runner. Audit SHA `0673f0e797cba64de7735a00973a02e483e6ac51326f35c2abd75327ed341cd8`. Source decision: `reports/capture/healthy-03_ai_review.json`.

Added generated captured report tables with explicit undefined denominators and separate claim-review status. Two regression cases passed. `make check` exited 0 with 204 Python tests plus frontend lint/typecheck/Vitest/build (`reports/phase13-check-final-report.log`). Full capture/evaluation gates remain pending the final run.


## 2026-10-12 — Phase 12: PASS

Completed all 24 frozen captures and explicit source-backed AI reviews, including all test rows. Final audit found no automatic quality issues. Public-only preparation produced 24 actual database exports. `make capture-stop` verified healthy controls and stopped all six containers with exit 0. Final regression: 204 Python unit/contract tests and frontend checks passed; 25 real database tests and one actual MiniLM test passed. Phase 12 report records final hashes, commands and limitations. Full captured comparison and claim review remain phase 13 work; no production or independent-human-reviewed claim.


## 2026-10-12 — Phase 13: PASS

Local checkpoint `a3bb1c2` preserves the completed capture and evaluation implementation. The full captured benchmark then completed 24 episodes / 120 new predictions in 103.19 seconds with 874,956 KiB peak RSS and no LLM calls. Archived 85 exact source files matching the run identity. Development candidate Hit@1 was 0/5 learned, 1/5 graph heuristic and 5/5 anomaly; learned abstained on every development/test case. Test ranking is undefined with zero identifiable episodes. Keep the existing heuristic default, with no frozen-model retuning.

Reviewed the fixed 25-output captured packet explicitly: 69 supported, 13 partial and eight unsupported structured claims. Output summaries/abstentions and zero path-claim coverage are described separately. Generated result tables and sanitized 24-episode EvaluationReport; actual schema validation and repeat byte equality passed. Phase report records all required gates and limitations. Proceed to phase 14 API/durable jobs, carrying grounding gaps forward as application release requirements. No GitHub push.


## 2026-10-12 — Phase 14: PASS

Implemented scoped versioned HTTP API, bounded multipart imports, durable investigation/import-to-snapshot jobs, idempotency/admission, cancellation/retry publication guards, real timestamps, safe audit/logging and sanitized report registration. OpenAPI/frontend types regenerated; owner/replay guide in `docs/API_LOCAL.md`. Loopback Uvicorn plus independent worker subprocess completed the real import/investigation workflow. Fixed READY retry validation, chunked 413 handling and stale migration test expectation. Final `make check`:214 Python tests plus frontend checks PASS. Final real regression:35 database tests PASS50.26s and MiniLM1 PASS4.00s (`reports/phase14-regression-final.log`). Local migration0006 applied successfully. Frozen benchmark artifacts preserved; grounding repairs remain a release requirement. No GitHub push. Proceed to phase15.


## 2026-10-12 — Phase 15: PASS

Built the actual React investigation dashboard with generated validators/types, scoped UTC replay, stale-response protection, graph/table/baseline timelines, exact-source drawers, saved exports, published evaluation cards, owner imports and four-method comparisons. Local isolated seed/launcher uses non-superuser runtime credentials; no external hosting. Final browser suite15PASS44.9s includes seven real workflow/state checks and eight clearly simulated UI boundaries. Actual pinned Ollama browser1PASS20.8s; first accepted output recorded1440input/523output tokens and19001.416ms generation.35real database tests PASS51.98s, MiniLM1PASS3.96s; final makecheck214Python/4Vitest plus lint/types/schema/build PASS. Screenshots inspected at360/768/1440 and axe/keyboard passed. Fixed race/hydration harness issues, landmark labels and graph highlighting; preserved the nominal healthy final-cutoff threshold crossing and added a separate explicit quiet-cursor fixture. Phase16 must repair known grounding gaps with a new runtime version, retain frozen benchmarks, and verify crash/recovery/security. No GitHub push.

## 2026-10-12 — Phase16: PASS

Repaired grounding with independently versioned runtime templatev6 and historical provenance notice; frozen ranker/benchmarks remain unchanged. Added durable generation fences, bounded transport, shared schema-probe admission, container restart reconciliation, job/saved-result snapshot retention, exact source membership migration0007 and real backup/restore/replay tools. Added deep-JSON and adversarial boundary tests plus actual SIGKILL drills.

Final makecheck222Python/4Vitest plus lint/types/schema/build PASS.40real database/API tests PASS99.42s;MiniLM1PASS4.00s.15browser tests PASS50.4s;actual native pinned-model browser1PASS26.3s with v6 provenance. Backup52artifacts/eight snapshots restored into isolated PG/Neo4j;10saved reports/35citations and one new report/six citations resolved. Worker lease expired naturally after SIGKILL; graph crash preserved old active snapshot. Actual diagnostic model request was dispatched, process killed, next call blocked, provider restart verified and generation resumed. Neo4j outage gave safe503 and recovered. Static internal-port and frontend-secret checks passed; release network/proxy execution remains18.

Known limits and corrected harness failures are in phase16report/SECURITY/RECOVERY/ADR0012. Dedicated-container recovery is automatic only after explicit provider restart; native uncertainty remains fenced. Recovery containers stopped, evidence retained. Dashboard recreated under tracecause-dashboard-phase16-20261012.service. No public deployment or GitHub push. Proceed17.

## 2026-10-12 — Phase17: PASS

Added pinned ordinary/manual CI, clean-source setup, measured performance gates,
private API metrics, deterministic fixture checks, scans and inventories. Actual
fresh Python3.12.13/npm/database volumes release-check exited0:230Python/4Vitest,
42real database tests85.41s,MiniLM1PASS3.68s,two cited fixture cutoffs,15browser
checks49.1s and four scan checks. Corrected unavailable uvPython3.12.15 pin, local
Node-only path, custom runner label, ML dependency retention and missing initial
fixture migration; failed logs retained. No remote CI run is claimed.

100k import baseline105.497s/40,223SQL calls→three repeats96–98s/423calls; all100k
stored rows/all columns have identical hashes. Corpus reuse preserves scope and
citations; lexical p95 improved1.096→0.585s in diagnostics. Final measured budget
run passed:96.942s import,692,992KiB RSS,lexical/graph p95 0.599/0.790s. Four real
queued HTTP jobs passed with concurrency2/one worker. Rejected multi-VALUES and
brief experiment overlap are documented. Clean volumes stopped and retained,
original dev services restored. Phase17report/CI/PERFORMANCE contain limits and
exact evidence. Frozen research preserved. No GitHub push. Proceed18.

## 2026-10-12 — Phase18: local PASS; public deployment BLOCKED

Built source-archived, content-addressed nonroot images and isolated release Compose.
Fresh volumes migrated/prefetched/seeded actual sources. PublicHTTP/TLS/browser,
private ports, no baked model/gold/secrets, operator worker and gracefulexit passed.
Fixed missing webdocs,inert CLI module and Caddyfile-capability startup failures.
Pinned Ollama0.40.2/Qwen real generation passed1416input547output tokens/no fallback,
173.4s near180s deadline; recorded CPU limitation. Dedicated provider stopped.

Restored31artifacts/one snapshot into newDB/emptygraph; replayed2reports/8citations
and fresh6citation template. Backupv2includes32artifacts. Verifiedv5→v4→v5 image
rollback at same schema0007 and database/app restart with unchanged saved identity.
Final233Python/4Vitest corePASS,43realDB/MiniLM testsPASS101.24s,four scansPASS.
Public replay remains loopback13000,worker/model stopped. No publicauthorization,
remotehosting/push,systemtrust modification or frozenresearch rerun. Full evidence
and limits:phase18report/OPERATIONS. Proceed19finalportfolioaudit.

## 2026-10-12 — Phase19: local PASS; public hosting not authorized

Completed acceptance audit, actual screenshots, README/cards, demo/case study,
interview/future-work materials and ADR0013. Found and repaired CLI learned-default
drift, normalized-hash/original-offset citation mismatch, and missing fresh-checkout
integration-fixture prerequisite. Runtime templatev7 preserves old report bytes and
frozen research; evidence inspection resolves correct scoped byte positions.

Fresh Python3.12.13/npm/newsecrets/newDBvolumes release-check passed243Python/4Vitest,
42realDB/API92.31s,MiniLM1PASS3.71s,15browser49.1s and four scanners. Initial13failed
integration tests retained; the Make prerequisite repaired the setup dependency.
Fresh training reproduced every model field except Python-version metadata; frozen
artifact unchanged. Final rootcheck245Python/4Vitest plus lint/types/schema/buildPASS.

Independent source audit resolved20citations, recomputed6requests/3errors and
17.5/23.75ms quantiles, verified6parent pairs and originalOTLPlineage. New restore
verified33artifacts/twosnapshots,4savedreports20citations and fresh6citationv7 output.
LocalHTTP/TLS/browser/imagepermissions passed; wrong-hostname TLS attempt retained.
All eight performance budgets passed (100k96.782s/423SQLcalls/692340KiB). Actual
native model first fell back on invalid support; unchanged same-request diagnostic
and subsequent fresh-request browser passed. Both failures and successes retained;
no first-attempt reliability claim. Native and pinned-container results distinct.

User-authorized review is AI-origin, not independenthumanadjudication. All20phases
implemented locally; no public deployment, GitHub push, frozen test rerun or
production/causal claim. See phase19report and RELEASE_AUDIT for complete evidence.

Final phase19-v2 image selection preserved saved identity at schema0007; actual
source/browser/container audit passed again. All four final scans exit0, actionlint
and diff checks pass,22 local Markdown links resolve. Local checkpoint closes the
implementation; no next numbered phase remains. Public release still requires
explicit owner authorization.

## 2026-10-12 — Complete Docker operator runtime: local PASS

User requested all application components start through Docker build/up. Added
compose.full.yaml with six runtime services and four gated startup jobs, persistent
random credentials, separate admin secret volume, automatic SQL/graph migrations,
pinned MiniLM/tokenizer/Qwen download and actual schema probe, bundled ranker
registration and idempotent source-fingerprinted seed. Added Docker Make shortcuts,
operations guide, ADR0014 and dedicated API/browser/lifecycle verification scripts.

Actual cold volumes downloaded models and booted successfully. Final images passed
three queued investigations, including real pinned-container Qwen:74.153s including
queue/polling,1626input/583output tokens,three citations,no fallback. Browser operator
login/job/citation passed after repairing its full-reload login loss; failure retained.
Actual multipart upload published a graph. Full stop/build/up preserved the operator
token,two active snapshots,and all five saved response hashes. Six runtime services
healthy,four startup jobs exited0; only web loopback14000 exposed,applicationUID10001,
read-only root/API artifacts,no admin secret mount in API/worker. Final248Python/
4Vitest plus lint/types/contracts/build and all four security scans passed. Existing
bundle-size/deprecation warnings remain. Evidence:reports/full-stack-v1 and
phase-reports/docker-full-stack.md. Existing development/release services separate,
frozen ranker/evaluation unchanged,no GitHub push or public deployment.

## 2026-10-12 — Evidence-plan generation and transfer ranker: local PASS

Owner requested architectural improvements instead of relying on invalid-answer
fallbacks. Added request-specific plan schemas, typed fact selection, independent
deterministic compilation and source validation. Qwen cannot author factual values,
citations, ranks or graph relationships. UI explicitly identifies AI fact selection
and server rendering. Retained local model pin, tokenizer/context/deadline/one-slot
fence, bounded192-token total output and one targeted repair. Legacy full-narrative
provider remains only for explicit offline diagnostic comparisons.

Final actual Qwen diagnostics:21/21base cases plus12/12new-wording calls valid on
the first attempt and expected focus. Initial21-call plan had6focus mistakes;
few-shot intent examples and request routing corrected those cases. Historical
comparison7/7 also passed, so no empirical failure-rate reduction is claimed.
Mean output tokens521.86legacy versus13.29plan; timing is diagnostic, not isolated
performance evidence. Initial failures and all successful calls remain retained.

Added candidate-logistic-v4, preserving v3 byte-for-byte. Fits50episodes/270rows:
40synthetic+10captured training cases, with grouped CV and train-only preprocessing.
Four graph-relative features, development-origin balanced selection and per-origin
rejection calibration. Captured development top1 improves0/5→5/5; synthetic10/10→8/10.
V4 bundled for explicit learned selection; heuristic stays application default.
No test evidence used for this fit/selection or new held-out result claimed.

Actual captured upload initially found missing earlier-cutoff snapshot support.
API imports now retain populated history buckets with bounded allocation, preserving
existing features and immutable saved snapshots. Final deployed captured check
ranks catalog first with six resolvable citations, no fallback,1533input/18output
tokens,62.620s pipeline and24.203s generation. The failed earlier run is preserved.

Final269Python/4Vitest plus lint/types/contracts/build,43realDB/API106.96s, four
scans, Docker API/browser and actual generation checks passed. Stop/build/up
preserved credentials, five snapshots and12saved responses. Six runtime services
healthy/four startup jobs exit0 at loopback14000; existing release/dev projects
separate. Source/models/docs reviewed; ADR0015 and phase-reports/reliability.md
record commands, all alternatives, evidence and limits. No GitHub push/public
hosting, no frozen benchmark rewrite, no production or causal accuracy claim.


## 2026-10-19 — Verified knowledge paths and portfolio closure

Owner authorized a broad KG/AI capstone audit and substantive implementation.
Starting clean checkpoint eca344b. Found runtime runbook selection using a text
marker rather than typed graph assertions, an API/UI exposing only service calls,
and sparse timelines allocating every missing ten-second point.

Implemented source-verified mentions/assertions (quote, offsets, predicate,
subject/support), bounded directed multi-hop authored-check queries, exact proof
paths, heterogeneous KG explorer, accessible table/zoom/source inspection and
same-snapshot temporal comparison. Template v8 records stricter check semantics.
No model retraining, fabricated infrastructure entities or causal claims. Sparse
chart gaps are bounded. Added safe exception-type diagnostics for worker dependency
retries; existing retry limits and error redaction remain.

Final gates: 288 Python/7 frontend tests plus lint/types/contracts/build; 44 real
DB/API tests; actual MiniLM; 16 browser cases; four scans. Controlled graph-query
suite: 21/21 expected checks vs 8/21 direct lookup across eight declared topologies,
zero extras; not diagnosis accuracy. Actual captured-dev v4/Qwen passed after one
dependency retry: catalog first, six citations, no fallback. Actual final operator
browser produced v8/Qwen without fallback in 34.601s pipeline, 29.124s generation.

Full stop/build/up preserved credentials, eight active snapshot pointers and
16 saved response hashes. Final runtime83 Python files and15 web assets match the
reviewed worktree/build. Six services healthy, four startup jobs exit0 at loopback
14000. New real KG screenshots inspected; README/case study/demo/interview/API/KG
materials updated, ADR0016 accepted. Earlier failed fixtures/harness attempts are
retained and explained in phase-reports/portfolio-readiness.md; artifacts under
reports/portfolio-20261019. Frozen models/research are unchanged. AI review only;
no independent production validation, no public deployment and no GitHub push.

## 2026-10-21 — Operational KG capstone and complete local release

The owner requested a richer operational/historical KG, varied topologies,
measured graph contribution, a professional UI, actual screenshots and videos, a
first-person production README and one-command full Docker startup. Implemented
source-backed deployment/version/configuration/runtime/host/ownership entities,
actual typed message handoffs, cutoff-scoped change and historical reasoning, and
three captured applications with six episodes. Public sources contain 28,935 spans,
129 operational receipts and 900 producer/consumer pairs; private controls remain
outside runtime images. React now exposes a source-inspectable graph/table,
change/onset evidence, prior incident state differences, responsive layouts,
imports, local Qwen investigation, saved results and exports. Three deployed
recorders generated 18 screenshots and three WebM videos from live workflows.

Frozen five-method API evaluation completed six changed and six pre-change
queries. Graph + temporal ranked the controlled reference event first in 6/6;
without graph ranked it first in 2/6, and without time also 6/6. All 1,021 source
citations resolved, and no future events appeared. The no-time tie and small
controlled corpus are disclosed. Initial runs stopped after eight and ten queries;
bounded indexed source lookup repaired the large-case failure without altering
reasoning policy or evaluator. Exact public results and input hashes are in
`docs/evaluation/operational/`.

Verification passed: 406 Python and 11 frontend tests plus lint/types/contracts/
build; 51 real PostgreSQL/Neo4j/API integration tests; four dependency/secret
scans after pinning `certifi==2026.10.19`; actual MiniLM and Qwen; three deployed
browser captures; isolated fresh-state restore of seven snapshots from 97 verified
artifacts with 195 scoped proof opens on each side; final stop/build/up preserving
operator credentials, 15 active pointers and 21 saved results. Running Python/web
bytes matched 94 source files and 17 built frontend files after rebuilding local docs.
The full source/audit media and restart records are summarized in
`docs/phase-reports/operational-capstone.md`. Frozen original research/model
artifacts remain unchanged. AI review only; no GitHub push or public deployment.
