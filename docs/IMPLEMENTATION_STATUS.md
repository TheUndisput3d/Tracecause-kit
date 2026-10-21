# Implementation status

| Phase | Status | Evidence |
|---|---|---|
| 00 Foundation | PASS | `docs/phase-reports/00.md` |
| 01 Contracts and configuration | PASS | `docs/phase-reports/01.md` |
| 02 Fixtures and evaluation protocol | PASS | `docs/phase-reports/02.md` |
| 03 OTLP ingestion | PASS | `docs/phase-reports/03.md` |
| 04 Persistence and jobs | PASS | `docs/phase-reports/04.md` |
| 05 Telemetry signals | PASS | `docs/phase-reports/05.md` |
| 06 Temporal knowledge graph | PASS | `docs/phase-reports/06.md` |
| 07 Documents and entity linking | PASS | `docs/phase-reports/07.md` |
| 08 Retrieval | PASS | `docs/phase-reports/08.md` |
| 09 Deterministic investigation | PASS | `docs/phase-reports/09.md` |
| 10 Structured generation | PASS | `docs/phase-reports/10.md` |
| 11 Candidate ranker | PASS | `docs/phase-reports/11.md` |
| 12 Captured telemetry | PASS | `docs/phase-reports/12.md`; all 24 captures audited, AI-reviewed and prepared; cleanup verified |
| 13 Evaluation and ablation | PASS | `docs/phase-reports/13.md`; complete separate synthetic/captured comparisons, ablations and explicit AI claim review |
| 14 API | PASS | `docs/phase-reports/14.md` |
| 15 Dashboard | PASS | `docs/phase-reports/15.md` |
| 16 Security and recovery | PASS | `docs/phase-reports/16.md` |
| 17 CI and performance | PASS | `docs/phase-reports/17.md` |
| 18 Deployment | PASS (local); public BLOCKED | `docs/phase-reports/18.md` |
| 19 Portfolio handoff | PASS (local, AI review) | `docs/phase-reports/19.md` |
| Complete Docker operator stack (follow-up) | PASS (local) | `docs/phase-reports/docker-full-stack.md`; frontend, API, worker, databases and AI start automatically |
| Evidence-plan generation and transfer ranker (follow-up) | PASS (local) | `docs/phase-reports/reliability.md`; constrained fact selection, deterministic compilation, experimental v4 and snapshot history repair |
| Verified knowledge paths and portfolio audit (follow-up) | PASS (local, AI review) | `docs/phase-reports/portfolio-readiness.md` |
| Operational KG capstone, interface, live media and full-stack release (follow-up) | PASS (local, AI review) | `docs/phase-reports/operational-capstone.md`; six captured incidents and complete frozen API comparison |

This table is an implementation checklist, not a claim that the planning kit's static checks verify application behavior.

All phases00–19 have local implementation evidence. Public hosting remains
unauthorized/not deployed. Review was delegated to the implementation agent and
is explicitly AI-origin, not independent human validation. See RELEASE_AUDIT.md
for requirement mapping and known limits.

At the earlier portfolio-readiness checkpoint, the operator stack included verified multi-hop authored-check queries,
heterogeneous evidence exploration and temporal comparison. Latest verification:
288 Python tests, 7 frontend tests, 44 real database/API tests, 16 browser cases,
actual MiniLM/Qwen and four scans passed. The restart check preserved eight active
snapshot pointers and 16 saved response hashes. Running Python/frontend bytes
match the reviewed local build. See the portfolio-readiness report for exact
commands, retained failures and artifacts.

The controlled KG competency suite finds 21/21 expected checks versus 8/21 direct
lookups across eight declared synthetic topologies. This is query verification,
not diagnosis accuracy. Experimental v4 improves captured development to 5/5 but
regresses synthetic development to 8/10; heuristic remains default. Original
frozen research and models are unchanged. Independent production validation and
public hosting are not claimed.

## Current local release — 21 October 2026

The richer graph connects captured deployments, versions, configuration,
runtime/host metadata and assigned ownership to measured service paths and
historical incidents. It distinguishes verified `CALLS` and `MESSAGE_FLOW` edges.
The React UI exposes change reasoning, graph/table, prior incident comparison,
source inspection, local Qwen reports, imports and exports. Docker Compose starts
the six runtime services and four gated initialization jobs.

The final gates passed: 406 Python unit/contract tests, 11 frontend tests, 51
real PostgreSQL/Neo4j/API tests, four advisory/secret scans, three actual browser
recordings, an isolated fresh startup/restore drill, and a stop/rebuild/start
preservation check. The frozen six-incident API evaluation completed 12 cutoffs,
resolved 1,021 citations and returned no future events. Graph + temporal placed
the reference event first in 6/6 changed cases; without graph scored 2/6 and
without time also scored 6/6. These are controlled local cases, not a production
accuracy estimate or evidence of added timing benefit in this corpus. See the
[completed capstone report](phase-reports/operational-capstone.md),
[evaluation record](evaluation/operational/results.json) and
[live media manifest](assets/operational/manifest.json).
