# Operational KG capstone expansion — 2026-10-19

Status: **LOCAL PASS — 21 October 2026.** The owner explicitly requested all three broader KG priorities,
a substantial UI redesign, an excellent full README with architecture/flows and
actual live screenshots/videos, and complete automatic Docker startup.

Starting verified checkpoint: 99f8958, clean tree. This follow-up supersedes the
previous narrow portfolio scope; earlier completion claims apply to that scope.

## Implementation sequence

1. Import exact deployment, software version, configuration, runtime/host and
   ownership receipts alongside telemetry; project typed, source-backed relations
   into immutable graph snapshots. Preserve old snapshot membership/replay.
2. Explain preceding changes through observed dependency/anomaly paths and compare
   eligible earlier incidents with explicit similarities, differences and citations.
3. Capture three actual application architectures and repeated chronological
   incidents, separate offline controls/gold, evaluate same-evidence baselines and
   graph/temporal ablations honestly.
4. Redesign the product around operational investigation, preserving accessible
   source inspection, replay, imports and reliable local AI.
5. Seed the richer captured cases automatically in the full Docker stack, verify
   end-to-end behavior and persistence, record actual screens and workflow video.
6. Write the final production-style README, architecture/flow diagrams, operations
   guidance, exact measured results/limits; update audit/build log/handoff.

No GitHub push or public deployment. Existing frozen research remains immutable.
All operational data are controlled local captures or explicitly authored registry
records, never represented as production data or independently human-adjudicated.

## Historical implementation checkpoint — 2026-10-19

The implementation now includes strict manifest-bound operational receipts, typed
Neo4j operational entities, actual producer/consumer `MESSAGE_FLOW` provenance,
cutoff-scoped change joins, latency/error onset and downstream degradation proofs,
and pinned historical snapshot comparisons. Earlier sources carry their own replay
mode and the current comparison/knowledge cutoff. History references block snapshot
retirement, and long operational reads renew their leases.

The public lab corpus contains six actual local process captures from three
architectures: **28,935 spans, 129 operational receipts, 900 message handoffs**.
The captures remain controlled experiments, with private controls excluded from
runtime sources. No production accuracy claim is made.

Completed checks at this intermediate checkpoint:

- Unit/contracts: **333 passed** in `reports/operational-capstone/unit-contract.log`
  before subsequent hardening additions. An initial run used host uv 0.13.0 and
  failed two tool-version checks; rerun with the pinned uv path passed.
- Real PostgreSQL/Neo4j integration: **47 passed** in
  `reports/operational-capstone/integration.log` before the later scope/renewal tests.
- Renewable lease integration: **1 passed**, with actual elapsed lease renewal and
  deleted-lease rejection, in `reader-renewal.log`.
- Operational admission regressions: **11 passed** (finite values, configuration
  key bounds, strict process IDs and runtime types).
- Separate cross-environment real database test passed (agent record).
- Interface: **10 unit tests**, **16 browser cases**, responsive/Axe checks; see
  `reports/operational-ui/browser-compact` and agent capture reports.
- Backend and frontend Docker images built successfully. Full stack startup with
  the new six-case bootstrap is still in progress; final source equivalence,
  lifecycle, actual Qwen, screenshots/video and README are **not yet complete**.

Independent review found and addressed configuration/history field bounds,
strict process IDs, environment scope, long-reader lease expiration, orphan
projection relationships and historical late-arrival semantics. The default demo
bootstrap also needs to preserve its existing active pointer when only the public
corpus fingerprint changes; that repair is in progress. The pre-upgrade state is
recorded in `reports/operational-capstone/pre-upgrade-state.json` (8 pointers and
17 saved result hashes).

Final verification must rerun against the final source. Frozen model artifacts and
original held-out research results remain unchanged. No GitHub push has occurred.

## Final review: defects found and repaired

These are engineering failures retained during this follow-up, not erased by
later passing checks. Original frozen research and model artifacts remain intact.

| Finding | Repair and retained evidence |
|---|---|
| Fractional replay cursor produced aggregate IDs absent from snapshot membership | New operational snapshots materialize closed ten-second cutoffs and pin their replay policy/modes. Old snapshots remain readable. `test_operational_api.py` exercises the actual fractional public cursor. |
| Unrelated MiniShop runbook could match a new application's short service name | New operational snapshots require explicit namespace/environment document scope; canonical names and aliases are resolved within it. Legacy snapshot policy remains pinned. |
| Corpus fingerprint change caused the original default demo to be reseeded | Idempotent bootstrap now preserves a valid original pointer. The original pointer was restored; replacement READY snapshot and saved result were retained. `default-pointer-restoration.json` and `original-state-preservation.json` record all eight original pointers and 17 saved result hashes. |
| Timeline counted multiple materializations of the same ten-second bucket | Select one intact eligible observation per service/start/end, with the greatest known sample coverage; preserve its source ID and statistics. No pooling of versions. `timeline-before.json` records 348 rows for 42 unique keys; database regression verifies uniqueness. |
| Graph canvas cropped nodes when findings narrowed its column | ResizeObserver refits untouched camera bounds; manual pan/zoom is preserved, and Reset restores automatic fit. Real Chromium/Cytoscape resize regression and the final browser suite pass. |
| First AI recorder expected domain-result ID to equal queued-job ID | The actual Qwen result succeeded without fallback. Recorder now checks the accepted outer investigation ID and exact equality of API result, export and downloaded JSON. Failed harness output remains in `ai-live-final/`. |
| First operational evaluation received HTTP503 on the first asynchronous case | `evaluation-v1` retains eight completed queries and 593 verified source resolutions; no aggregate PASS was produced. The same request subsequently succeeded. A redundant quadratic handoff join was removed while preserving full edge/source validation and five-second query bounds; full captured-message and tampering regressions passed. The transient failure's precise cause is not proven. Ranking policy and evaluator code were not changed. |
| Isolated recovery helper could not preserve copied cache metadata | Narrow FOWNER capability added to the temporary cache-copy helper. Failed fresh v1 artifacts retained; fresh v2 startup passed. |
| Host recovery harness lacked its isolated operator token and diagnostic DB connectivity | Host settings now reference the new private token file. A dedicated loopback diagnostic bridge is used only by the isolated drill DBs; normal Compose exposure remains web-only. Failed stage logs retained. |

An earlier `test_operational_api.py` run used the wrong test URL (`/api/v1/series`)
and returned404. Correcting the test to the implemented episode-series route
produced a passing full 50-test database/API run; `timeline-regression.log` retains
the failed harness run. Another intermediate Compose wait was interrupted by a
concurrent web refresh; final lifecycle verification is run sequentially.

## Historical pre-release verification checkpoint

- `make check`:401 Python and11 frontend tests, plus formatting, lint, types,
  generated contracts and production frontend build; `check-complete.log`.
- `pytest tests/integration -q`:50 real PostgreSQL/Neo4j/API tests in485.18seconds;
  `integration-release.log`.
- Actual pinned MiniLM execution:1test passed; `minilm-final.log`.
- Final frontend browser suite after canvas repair:16passed;
  `reports/operational-ui/browser-resize-final/`.
- Four advisory/secret scans passed in `scans-final/summary.json` before final
  media and recovery-helper additions; final rescan remains scheduled.
- Original-state audit preserved8active pointers,17saved result hashes and the
  operator credential. Primary stack contains14episodes after six new imports.
- Isolated freshv2 startup:6healthy services,4initializers exit0,7episodes and
  actual local MiniLM/Qwen readiness. Full restore verification is still running.

The completed verification and retained failures follow below. Earlier checkpoint
counts above are historical and do not describe the final source.

## Final local release — 21 October 2026

The completed build includes source-backed deployments, versions, configurations,
runtime/host identities, assigned ownership, typed synchronous calls and actual
producer/consumer `MESSAGE_FLOW`. Change review joins directed dependency paths
to measured onset; historical comparisons pin each earlier snapshot and cite
both sides of state differences. The three captured applications contain 28,935
spans, 129 operational receipts and 900 source-linked message handoffs. Their
private intervention controls are absent from the runtime image.

The final React workspace includes the episode library, change reasoning, typed
graph/table, source drawers, measured signal and historical comparison views,
replay, dark mode, responsive phone/tablet/desktop layouts, imports, saved results
and actual local Qwen investigation/export. Three deployed browser recorders passed
and published 18 screenshots plus three WebM videos in
[`docs/assets/operational`](../assets/operational/manifest.json). The Qwen run
recorded three hypotheses, five cited sources, no fallback, 36.562 seconds of
generation and a verified saved API/downloaded export.

| Final gate | Result | Retained local evidence |
|---|---|---|
| Code, contracts and frontend | 406 Python + 11 frontend tests; lint, formatting, mypy, generated contracts and production build passed | `make check` output in this run; preceding passing log at `reports/operational-capstone/check-final-release.log` |
| Real persistence/API | 51 PostgreSQL/Neo4j/API integration tests passed in 286.40 seconds | Test runner output; prior passing run in `integration-complete.log` |
| Frozen operational comparison | Six changed + six pre-change cutoffs; 1,021 citations resolved; zero future events | [Published results](../evaluation/operational/results.json), [input hashes](../evaluation/operational/frozen-inputs.json) |
| Security | Working-tree and Git-history secret scans, Python and npm audits all passed after pinning `certifi==2026.10.19` | `reports/operational-capstone/scans-release-final/summary.json` |
| Isolated fresh startup and restore | Six healthy services, four successful init jobs; seven snapshots rebuilt from 97 verified artifacts; 195 scoped source proofs reopened on each side; application-state digest identical before/after | `reports/operational-capstone/fresh-restore-20261019-v2/report.json` |
| Final full-stack restart | Credential, 15 active snapshots and 21 saved results preserved; six healthy services, four successful init jobs | `reports/operational-capstone/lifecycle-final-20261021/report.json` |
| Running source equivalence | API and worker match 94 Python files; served web matches 17 local built files | `reports/operational-capstone/source-audit-release.json` |

The five-method comparison selected the reference event first in 6/6 changed
cases for graph + temporal, 0/6 chronological, 0/6 direct service, 2/6 without
graph and 6/6 without time. Graph and no-time tied on these scenarios; no
incremental temporal ranking lift is claimed. Pre-change abstentions were 6/6
for graph + temporal, graph-only and no-time, but those cutoffs may lack eligible
degraded signals. Six controlled local cases do not establish generalization or
causal correctness. The full per-case/citation record and limitations are in
`docs/evaluation/operational/results.json`.

The first evaluation stopped after eight cases/593 citations with HTTP503. The
second stopped after ten cases/807 citations at an explicit support limit in the
largest asynchronous episode. The source-backed, bounded retrieval repair is
documented in [large-case-retrieval-repair.md](../evaluation/operational/large-case-retrieval-repair.md).
The reasoning policy and evaluator remained frozen. A first source-audit attempt
after the final restart compared the new web image with a local build made before
the last documentation edits; rebuilding the local frontend reproduced the served
17 files exactly. These failed/intermediate artifacts are retained, not counted
as successful final results.

No GitHub push, public hosting, independent human review or production incident
claim is part of this local release.
