# TraceCause final local checkpoint — 2026-10-21

## Current release: operational KG capstone — local PASS

The broader graph, historical comparisons, three captured application topologies,
redesigned workspace, actual live screenshots/videos and complete Docker Compose
startup are implemented. The final README is written in the owner's first-person
project voice. Start at [README.md](../README.md) for the product walkthrough,
diagrams, media and exact measured comparison. The detailed release record is
[operational-capstone.md](phase-reports/operational-capstone.md); complete API
evaluation and source citation hashes are in
[results.json](evaluation/operational/results.json).

Final gates: 406 Python and 11 frontend unit/contract tests, 51 real database/API
integration tests, four scans, three deployed browser captures, six healthy Docker
services and four successful init jobs. The final stop/build/up preserved the
operator token, 15 active snapshots and 21 saved results. An isolated clean-state
restore independently rebuilt seven snapshots from 97 verified artifacts and
reopened 195 scoped proofs on each side. The frozen operational comparison covered
12 cutoffs, resolved 1,021 citations and reported zero future events. Graph + time
and graph without time both placed the controlled reference event first in 6/6
changed cases; removing graph paths yielded 2/6. That tie and the small local
corpus are explicit limits. No GitHub push or public deployment occurred.

The remaining sections are historical checkpoints retained for audit context.

## Earlier follow-up: functional KG and portfolio readiness — local PASS

Read docs/phase-reports/portfolio-readiness.md, docs/KNOWLEDGE_GRAPH.md and ADR0016.
The owner explicitly broadened the scope to make the actual AI/KG engineering
portfolio worthy. Source-verified assertions and directed three-hop runbook-check
joins now drive inspectable KG paths; heterogeneous graph/table/source UI, temporal
pin comparison and bounded sparse charts are implemented. Template v8 records
stricter check selection. Existing LLM evidence plans and v4 model are preserved.

Final verification: 288 Python/7 frontend tests, 44 real DB/API tests, actual MiniLM,
16 browser cases, four scans. Eight controlled topology cases retrieved 21/21
expected checks versus 8/21 direct lookup; not diagnosis accuracy. Final Docker
Qwen browser: no fallback, 34.601s pipeline. Restart preserved credentials, eight
active snapshot pointers and16 saved results. Runtime Python/frontend bytes match
the reviewed source/build. All artifacts in reports/portfolio-20261019; selected
actual screenshots and competency output copied into docs/assets.

Full application remains healthy on http://127.0.0.1:14000. No GitHub push or public
deployment. Original master authorizes verified local commits. Independent human
review and production-data breadth remain disclosed research limits. No required
implementation phase remains open for this local capstone scope.

## Previous follow-up: evidence plans and transfer ranker — local PASS

The owner requested actual architecture improvements to LLM reliability and learned
transfer. Production Qwen now emits only a typed focus and up to three verified
fact IDs. Independent code compiles the factual narrative and validates sources;
UI identifies AI selection versus server rendering. Prompt `plan_v1.txt`, protocol
`evidence-plan-v1`, at most192 output tokens total over two attempts. Model pin,
local endpoint allowlist, bounded deadline and uncertain-call fence remain. Legacy
full-report generation is available only for explicit offline comparison.

V4 adds four graph-relative features and fits40 synthetic+10captured training
episodes/270candidate rows. Hash76b66e66d9c5686eecced3cd82751977f91b961740a3f5521ffe3448cda26811.
Captured development improves0/5→5/5; synthetic declines10/10→8/10. Heuristic remains
application default, v4 is an explicit experimental option. Development was used
for selection/calibration; no new held-out test claim. V3 is preserved and refitted
byte-for-byte. Training/review data stay offline; Docker loads only bundled weights.

Actual captured API verification found baseline/final-only snapshot observations
could not support an earlier cursor. New API imports retain populated intermediate
buckets with a19,000 observation bound; feature windows/old snapshots stay intact.
First failure is retained in reports/reliability-v1/captured-api. After repair and
final Docker restart, captured-api-final passed:catalog first, six resolvable
citations, no fallback,1533input/18output tokens,62.620s pipeline/24.203s generation.

Verification:269Python/4Vitest plus lint/types/contracts/build;43realDB/API tests;
four scans; actual operator browser and API;33/33 final Qwen diagnostic calls valid
on first attempt with expected focus. Initial plan had6focus mistakes, retained.
Historical full-report comparison7/7 also passed, so no measured failure-rate
reduction is claimed. Structural factual-mutation prevention is the improvement.
Restart preserved credentials, five snapshots and12 saved responses; six healthy
services/four successful startup jobs at http://127.0.0.1:14000. Later verification
adds saved results; restart counts describe the actual checked checkpoint.

Evidence reports/reliability-v1; ADR0015; phase-reports/reliability.md; updated model
card and generation/Docker guides. Private captures/prepared review bundles are
needed to reproduce the v4 training bytes; clean Docker inference needs none.
Public hosting and GitHub push remain unauthorized. No new numbered phase is due.

## Previous follow-up: complete Docker operator runtime

The user requested all frontend/backend/worker/KG/AI components start with Docker.
Implemented `compose.full.yaml`; run `docker compose -f compose.full.yaml up --build
-d --wait --wait-timeout 2400` from the repository root (one shell line).
Project `tracecause-full` is running at http://127.0.0.1:14000 with six healthy
runtime services and four successful startup jobs. Persistent random credentials,
schema/model/bootstrap and trained ranker registration need no manual setup.
Use `docs/DOCKER_FULL_STACK.md` for token login and Ollama selection.

Evidence: `reports/full-stack-v1`, report `phase-reports/docker-full-stack.md`,
ADR0014. Final check248Python/4Vitest, all four scans, actual3queued investigations,
browser, multipart import, full stop/up and container boundary checks passed.
Pinned container Qwen:74.153s end-to-end,1626input/583output tokens,3citations,
no fallback. Two active snapshots and five saved response hashes unchanged across
restart; operator token preserved. Initial browser harness reload/login failure
retained and repaired in the script. Existing release/dev projects remain separate.
No source model/frozen evaluation change and no GitHub push. This follow-up is
complete; it does not reopen a numbered phase or authorize public hosting.

## Status and authority

Phases00–19 implemented and verified locally. Phase18 public hosting remains
BLOCKED because publication was not authorized. The user delegated review to
Codex; reports explicitly say AI review, not independent human adjudication.
**Never push GitHub without explicit permission.** Verified local commits were
authorized by the original master. No next numbered implementation phase remains.

Read IMPLEMENTATION_STATUS, RELEASE_AUDIT, phase-reports/19, BUILD_LOG and ADR0013
before changing anything. Frozen benchmark/model/capture artifacts must remain
unchanged; new diagnostics need new output directories. Known model variability,
negative transfer, undefined captured test ranking and small-sample limits are
recorded in KNOWN_ISSUES, MODEL_CARD and EVALUATION_RESULTS.

## Last verification

- Fresh checkout reports/phase19-clean-v1/source: Python3.12.13, new venv/npm,
  credentials and database volumes; host download caches reused. Full release-check
  passed243Python/4Vitest,42realDB/API,1MiniLM,15browser and four scans. Initial13
  fixture-missing failures and explicit source-fix manifest retained separately.
- New train/dev report matches frozen model in every field except Python metadata.
  Original a824ecaef074ec37402b0caa7bf880c92929e57171b973ff2e0e9e72feec277f unchanged.
- Final root makecheck245Python/4Vitest plus lint/types/contracts/frontendPASS.
- Actual source audit20citations, originalOTLPlineage,6parent pairs and independent
  payment6requests/3errors/p50=17.5/p95=23.75. v7positions are exact normalized bytes;
  historical saved positions preserved with UI notice and resolved on inspection.
- New backup33artifacts/twosnapshots restored into newDB/graph, replayed4results/
  20citations and computed a fresh6citationv7 investigation.
- LocalHTTP/TLS/browser/imageaudit passed; all eight performance tolerances passed.
- Native Ollama0.18.3 first produced invalid support→visible template fallback;
  same request diagnostic then passed unchanged, followed by fresh browserPASS24.4s,
  actual generation19.317s/1648input598output/no fallback. Failures remain recorded.
  Pinned-container Ollama0.40.2/Qwen proof remains phase18 (173.4s). No paid provider.

## Services and retained data

- Local read-only release http://127.0.0.1:13000, Composeprojecttracecause-release.
  Immutable selected images are recorded in ignored .env.release and reports/releases.
  Release worker/model stopped; PG/Neo4j internal-only; never print .secrets values.
- Developmentdashboard http://127.0.0.1:15173/API18080, transient user unit
  tracecause-dashboard-phase19-20261012.service. DevelopmentPG15432/Neo4j17687.
  NativeOllama11434 belongs to the existing host setup; don't stop/reconfigure it.
- Cleanprojecttracecause-ci-clean-v2 stopped, volumes/source/evidence retained.
- Restoreprojecttracecause-release-restore-v2 stopped, newDB
  tracecause_restore_release_v2 inside releasePG; artifacts under
  reports/phase19-release/restored-artifacts-v1. Earlier restore projects retained.
- Unrelated host Docker applications exist. Avoid blanket stop/delete/prune commands.

Current source has templatev7 and heuristic CLI/API/UI defaults. No schema migration
from0007. Opening saved results never runs a model; fresh diagnostics have distinct
IDs. Never rewrite historical metadata to make a benchmark look repaired.

Final selected release: phase19-v2, with exact source/image manifest in
reports/releases/phase19-v2/manifest.json. Final rollout preserves saved identity;
source audit/browser/image audit passed. All four final scans exited0. Repository
closure is the local commit titled "Complete release audit and repair citation byte provenance".

## Active expanded operational capstone work — 2026-10-19

User authorized all three KG improvements, then a polished UI, actual live
screenshots/videos, a comprehensive final README and complete Docker startup.
Continue autonomously. No GitHub push/public deployment. Starting verified commit
was `99f8958`; current changes are uncommitted and shared with three agents.

Current full Docker stack is healthy at **14000**, with **14 episodes** including
six new actual captures (28,935 spans,129 receipts,900 message handoffs). It is an
intermediate image: final source equivalence and release checks remain pending.
The original default active snapshot `6385ba99-2ab2-48b9-834c-d46892c55082` was
restored after a corpus fingerprint change caused unwanted demo reseeding. The
replacement READY snapshot/result were retained; no historical data was deleted.
Exact restoration and original 8 pointers/17 result hashes are under
`reports/operational-capstone/`.

Current blockers to final validation (fixes in progress, not external blockers):

1. Operational signal identity includes raw as_of_ns. Public default cursors have
   fractions, while imports materialized whole 10s cutoffs; signal IDs therefore
   disappear when checked against snapshot membership. Parent added
   `GraphMaterial.operational_replay_version` with conditional hash/membership,
   and operational reads canonicalize only `closed-bucket-v1` snapshots. Capture
   agent owns replay helper, inclusive-final cutoff materialization, ApiWorker
   integration and idempotent migration of six new active snapshots. Preserve
   old snapshots and migrate chronological so history pins updated earlier ones.
2. Runbook short-name linking can attach unrelated MiniShop checkout docs to the
   new app. History agent owns optional explicit service_scope namespace/env in
   document frontmatter and load_document_material(require_explicit_scope=...).
   Parent already moved operational source loading earlier in graph builder and
   pins `document_scope_version=explicit-service-scope-v1` for new operational
   snapshots; membership-backed old snapshots keep original semantics/hash.
3. Capture agent also adds public default symptom service metadata. Parent added
   EpisodeView.default_symptom_service_id and API output. Interface agent must
   default to it instead of arbitrary graph hash order.

Agents and owned work:

- operational_capture: actual capture done/audit PASS; operational seed/full_stack
  marker-preservation fix and docs/architecture/operational-graph.md + ADR0017 done;
  now replay helper/migration and default symptom.
- history_reasoning: source-verified MESSAGE_FLOW, deterministic change frontier
  and historical comparisons, tests/evaluator; now document scope. Algorithm frozen
  pre-evaluation: reasoning.py SHA
  `6db208a4e90a2983b5021ecf782778abba23a9fc1b799a8789ab4228e31654db`, protocol files
  in reports/operational-lab. Deployed image differs; do not run final evaluator
  until exact source hash matches. No actual offline evaluation run yet.
- interface: polished shell and compact composer, operational graph/proof/history
  UI, 10 unit +16 browser checks, actual preview media at
  reports/operational-ui/live-preview. Must record final media after fixes. Preview
  server15174 proxies14000. Owns apps/web scripts operational-showcase.mjs and
  ai-showcase.mjs. AI script accepts token only via env TRACECAUSE_DEMO_TOKEN;
  capture internally from Docker, never print. Current screenshots truthfully
  show missing signals; replace final portfolio assets only with fixed live data.

Parent integration: strict source models, ingest/storage, typed operational graph
projection/verification, full snapshot hooks, pinned history retirement, renewable
leases, API routes/contracts. New operational source/evidence and handoff routes
under /api/v1/operations. Historical prior profiles use EVENT at prior.end; AVAILABLE
current requests require all earlier source availability <= current cutoff. Each
history citation carries its own temporal_mode and knowledge_as_of. LLM remains
measurement/runbook fact selection; operational changes/history are deterministic,
source-verified reasoning, not newly LLM-authored conclusions.

Checks so far: 333 unit/contracts passed before newest tests; 47 real DB/API tests
passed before new scope/renewal tests; separate crossenvironment test PASS and
renewal test PASS; admission model regressions11 PASS. Final make check/all
integration required after current fixes. Use pinned uv PATH below; host uv0.13
fails repo version constraints. Detailed intermediate failures/results are recorded
in operational phase report.

Next after source repairs:

- Add/run regression for fractional default cursor yielding nonempty grounded
  degradation signals (`tests/integration/test_operational_api.py` now asserts it).
- Regenerate contracts, final frontend build, final API+web image build; up/wait
  migrates six new snapshots without touching original8 pointers.
- Source audit verifies all API/worker .py and served UI bytes, then one controlled
  operational evaluation; retain failures, don't retune on six known cases.
- Actual final UI reviews/media (changes, graph, receipts, history, async path,
  real Qwen inference and export), screenshots/video artifacts.
- Docker lifecycle/state preservation, fresh app/database startup (can reuse
  disclosed verified model download caches), restore including operational sources,
  scans, actual MiniLM and Qwen checks.
- Write final README only using verified feature/results/media; diagrams and links.
  Update BUILD_LOG/STATUS/HANDOFF/phase report. Local commits authorized by original
  master only after verification; no push.

Commands/environment:

```
cd /home/aayush/Documents/proj/kg/TraceCause_Implementation_Kit/tracecause-kit
export PATH="$PWD/.tools/node_modules/node/bin:/home/aayush/.cache/uv/archive-v0/02Rv7i6dB1RmEAjA/bin:$PATH"
export UV_NO_SYNC=1
```

Build efficient: `docker compose -f compose.full.yaml build api web` then
`docker compose -f compose.full.yaml up -d --wait --wait-timeout 2400` (log to
new reports). Backend shared image avoids redundant builds. Default stack14000;
older readonly13000, devPG15432/Neo4j17687, nativehostOllama11434 and unrelated
social containers must remain intact. Disk~102GB free, memory23GiB host/10GiB
available at checkpoint. Do not blanket prune/stop other containers.

## Superseding operational checkpoint — 19 October, 05:51 UTC

The earlier active section's three source repairs are complete: closed-bucket
replay, explicit document scope and public default symptom/cursor. All six active
operational snapshots have migrated; old snapshots/results remain intact. Latest
complete checks: `check-complete.log` **401 Python/11 frontend PASS**;
`integration-release.log` **50 real DB/API PASS,485.18s**; actual MiniLM PASS;
interface final resize browser suite **16 PASS**. Four scans passed before final
media/tooling additions. Files remain uncommitted. No push.

Remaining work is final verification/packaging, with two genuine issues found:

- Frozen operational API evaluation `reports/operational-lab/evaluation-v1`
  completed8/12 queries and593 source resolutions, then Media1 operations returned
  HTTP503 after11.6s. Same unchanged request later returned200; direct invocation
  took14.36s. History agent is removing a quadratic handoff verification join while
  retaining exact full edge/source checks and5s transaction limits. Reasoning policy
  SHA6db208a4e90a2983b5021ecf782778abba23a9fc1b799a8789ab4228e31654db remains frozen.
  Preserve this failed evaluation, rerun after deployment, disclose the execution
  repair and never retune scoring on these cases.
- Isolated fresh stack `tracecause-proof-20261019-v2` startup PASS:6healthy services,
 4successful jobs,7episodes, actualMiniLM/Qwenprobe. It reuses copied verified model
  download caches with fresh app/DB/graph/artifact/credential volumes. Its writers
  andOllama stopped forbackup. Recovery harness token-file/internalnetwork port
  issues being fixed by capture agent; database backup/restore is not yet passed.
  Evidence under `reports/operational-capstone/fresh-restore-20261019-v2` includes
  private secrets/backup: never add that directory togit.

Parent fixed timeline duplicate bucket versions:348→expected42unique(service,start,end)
points for ParcelShop2. SQL selects one intact eligible measurement with greatest
sample coverage, preserving its citation; never sums versions. Regression passed.
Interface fixed graph canvas ResizeObserver/autofit when findings narrow its column,
manualpan/zoom preservation, font weight and sectionscroll. These fixes are built
in `docker-build-polish.log`, but primary14000 still serves priorimage until latest
handoff retrieval repair is built/deployed. Do not record final media yet.

First actualQwenrecording generated3hypotheses/5citations withoutfallback:
job84c2ce7e-e59c-4a7b-bd14-672be9a28fa8,generation30.661s,pipeline42.168s.
Recorder failed on incorrect nestedID==jobID assertion; fixed to compare exact
export/API/browserdownload. Real screenshots exposed canvas/timeline defects above.
Retain failed harness output `ai-live-final`, record freshv2 afterfinaldeployment.
Token is password-masked in UI and obtained internally viaenvironment, never logged.

Next: history agent finishes efficient handoff validation/real450+pair test; parent
builds/deploysapi/worker/web sequentially, sourceaudit andliveuniquetimelinecheck.
Then history runs newfrozenevaluation whileinterface recordsactualops/history/async
media andparentrecordsactualAIv2. Capture agent continues isolatedrestore.
READMEownedbyhistoryagent has complete architecture/content butfinalmedia/results
TODOs; publish sanitized offline targetlabels underdocs/evaluation only, excluded
from Dockercontext, soexact evaluation is reproducible without rawprivatecontrols.
Parent stillmust run full_lifecycle afteractiveevaluation/media finish, finalscans,
link/diffreview, finalBUILD_LOG/STATUS/RELEASE_AUDIT/phase report/HANDOFF updates and
verified localcommit ifmasterauthorization holds. NoGitHubpush/publichosting.
