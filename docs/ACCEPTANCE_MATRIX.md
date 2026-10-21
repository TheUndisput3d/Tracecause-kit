# Requirements, phases and evidence gates

Every row requires executable behavior and observed verification. Status starts NOT RUN; the build log must never convert missing infrastructure into PASS. Core invariants apply to every phase.

| ID | Requirement | Phase | Required evidence |
|---|---|---|---|
| R01 | Locked open-source-first skeleton and dependency choices | 00 | doctor, lockfile resolution, backend/frontend builds; real DB connection |
| R02 | Validated config and typed contracts | 01 | JSON Schema/Pydantic/OpenAPI parity and invalid-input tests |
| R03 | Reproducible fixtures and frozen group split | 02 | same seed/hash, split isolation, no target metadata leak |
| R04 | OTLP IDs/timestamps/redaction | 03 | hex IDs, integer ns, malformed/duplicate/late span property tests |
| R05 | Durable artifacts, jobs and authoritative persistence | 04 | real PostgreSQL, reimport, lease recovery and rollback |
| R06 | Correct trace-derived signals | 05 | server/client separation, quantiles, missingness and baseline checks |
| R07 | Temporal provenance-bearing graph | 06 | real Neo4j paths, no unsupported edges, failed build keeps active snapshot |
| R08 | Documents/aliases with exact citations | 07 | offset round-trip, ambiguity, invalid predicate quarantine |
| R09 | Fair dense/lexical/hybrid/graph retrieval | 08 | real MiniLM, eligible-only corpus, caps, RRF and time/scope adversaries |
| R10 | Working deterministic investigation | 09 | CLI import-to-citation workflow, healthy and insufficient states |
| R11 | Actual local open-weight generation | 10 | real Ollama schema probe + audited outputs, invalid-output fallback |
| R12 | Trained candidate scorer with honest comparison | 11 | grouped fitting, exported inference parity, actual model/dev report |
| R13 | Actual controlled telemetry capture | 12 | five services, real trace pairs, interruption reset, hashed captured bundles |
| R14 | Full evaluation with baselines and uncertainty | 13 | frozen test run, per-episode scores, grounding audit, separate origins |
| R15 | Complete versioned API | 14 | auth, paging, jobs, series, evidence, saved results and idempotency |
| R16 | Usable frontend backed by actual data | 15 | Playwright, all citations, cursor race test, mobile/accessibility screenshots |
| R17 | Safety, resource controls and recovery | 16 | injection, traversal, oversized input, no public mutations, rebuild |
| R18 | Reproducible CI and measured performance | 17 | genuine workflow results, no mock masquerading, resource/latency records |
| R19 | Containerized operations and deployment | 18 | fresh volumes, real Docker health, secrets, restore and rollback rehearsal |
| R20 | Portfolio evidence and final audit | 19 | honest README/demo/result table, license/data/model cards, release verdict |

## MVP versus flagship

MVP: phases 00-10 plus the core API/UI subsets of 14/15 and local safety checks in 16. Defer trained ranking, captured evaluation, comparison dashboard and public deployment. Mark those omitted requirements NOT IN MVP, not PASS.

Flagship: all 20 rows, with actual training and actual captured telemetry. Public hosting itself may remain NOT DEPLOYED when no host/publication permission exists; local deployment/restore still must pass. State this clearly in the portfolio. Optional Astronomy Shop capture, ANN acceleration, document LLM extraction, cross-encoder reranking and GPUs are not completion requirements.

## Non-negotiable regression suite

No future/gold leakage; no unsupported call edge; no invalid citation; no cross-snapshot result; no rank altered by the LLM; no unbounded traversal; no opaque fake integration; no unapproved publication; no hidden paid service; no fabricated metric. A discovered violation blocks release until repaired.
