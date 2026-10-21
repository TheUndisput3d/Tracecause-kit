# Frozen synthetic evaluation protocol (v1)

**User update, 11 October 2026:** Codex is authorized to perform the reviews and proceed. Use explicit `review_origin=ai`; independent human review was not performed. This supersedes the human-only gate below; see ADR 0010.

Frozen on 2026-10-10 before retrieval features or learned ranking. This document defines eligibility and denominators; it contains no measured system scores. `scripts/fixtures.py` is the evaluator-side source of the exact group assignment and seed derivation. Its generated `data/gold/seed-20261010/dataset.json` stores the private group table and per-episode truth hashes. Runtime images copy neither `scripts/` nor `data/`.

## Dataset and split

The default seed is 20261010. There are 24 scenario groups, five episodes each: train groups g00–g13 (70 episodes), development g14–g18 (25), and test g19–g23 (25). Healthy groups are g02/g07 in train, g16 in development and g22 in test. Test uses held-out topology C; A and B occur only in train/development. The group is a topology, mechanism, target role and traffic-profile combination, not an individual trace or seed. No test result may be used to select features, hyperparameters, thresholds, prompts or a candidate version. The test protocol is run once after those choices are frozen; exploratory reruns must be labeled.

Split-integrity checks reject duplicate public source hashes, repeated episode IDs, altered truth hashes, cross-split group IDs, and declared `paraphrase_of` or `adjacent_window_of` relations crossing splits. If future paraphrases, aliases, repeated seeds or adjacent cutoffs are added, assign them the same split and record those relations in the private index before generation. The current generator has no question paraphrases or adjacent-window derivatives. The episode windows are disjoint by construction. Do not split individual spans, traces or related windows independently.

## Questions and judgments

Author questions in six categories: direct fault-location lookup, dependency-path explanation, temporal change, related incident/document retrieval, ambiguous diagnosis, and unanswerable/out-of-scope. Each question must carry an opaque question ID, episode ID, split, cutoff, category, provenance of authorship, review status, and explicit answerability. All proposed questions require independent human review before inclusion in a scored benchmark. The initial examples in `data/evaluation/annotation_examples.json` are drafts authored during implementation and **not human verified**. The CSV template is in the same directory.

The private episode judgment contains an accepted target-service set, `identifiable` flag, mechanism, intervention interval, observable evidence locators, and a rationale. A target intervention is not automatically identifiable from telemetry. For a healthy episode, the accepted set is empty and the expected response is healthy/abstain. For ambiguous or unidentifiable episodes, preserve the acceptable set and rationale, but exclude them from unique-target accuracy and include them in abstention analysis. The simulator provisionally marks timeout and volume cases unidentifiable; these judgments still need review before final evaluation. Retrieval judgments use grade 0 (irrelevant), 1 (useful context), or 2 (directly supports a factual answer), with exact source hash, trace/span or document offset, evaluator ID, disagreement notes and review status. A generated LLM judgment is never accepted as gold by itself.

## Metrics and denominators

Graph edge precision divides correctly reconstructed directed call edges by all reconstructed edges; recall divides by all observable known edges in that episode. Missing-parent cases are reported separately. Entity-link precision/recall use a reviewed mention set, with unresolved mentions counted and reported.

Candidate recall divides identifiable faulty episodes with at least one accepted target in the generated candidate list by **all identifiable faulty episodes**. End-to-end Hit@k divides identifiable faulty episodes with an accepted target in the top k by that same denominator. Conditional Hit@k divides by identifiable faulty episodes where a correct target was generated. MRR uses the first accepted target rank and assigns zero on a miss, over all identifiable faulty episodes. Report exact counts for healthy and unidentifiable cases separately.

Retrieval Recall@k divides the number of judged relevant items retrieved in the top k by the number of all judged relevant eligible items for that question. nDCG@k uses 0/1/2 grades and the ideal ranking of the judged eligible pool. A question with no relevant judged items is excluded from these two metrics and counted in the unanswerable set. Report the fraction of retrieved items that were judged; do not silently treat unjudged items as irrelevant. The same eligible corpus, cutoff and context budget apply to graph and non-graph retrieval comparisons.

Citation validity divides exposed citation IDs resolvable in the same snapshot/scope/cutoff by all exposed citation IDs. Numeric consistency divides verified numeric claims by all checkable numeric claims. Human citation precision divides supported cited claims by all reviewed cited claims. Claim coverage is the fraction of material answer claims carrying an appropriate citation. Abstention reports answered coverage and error rate among answered questions separately for healthy, ambiguous and unanswerable categories. Report p50/p95 stage latency, provider use, failures, context tokens and cache hits, with cold/warm runs separated.

Aggregate confidence intervals use paired bootstrap resampling of **episodes**, not spans or multiple questions from one episode. Fix and record a bootstrap seed and 1,000 resamples as the initial procedure. Always show numerator/denominator and split/origin. Synthetic and eventual captured-demo results remain separate; no production generalization follows from either.

## Evidence isolation

Only public `index.json`, per-episode `manifest.json` and declared `traces.jsonl` are importable. The runtime import allowlist rejects sidecars, symlinks and undeclared files. Private `data/gold/` is ignored by Git and Docker and is never imported by runtime modules. Generic runbooks may describe diagnostic procedures but must not identify a test episode's hidden target. Before scored evaluation, scan authored materials for target/injector leakage and have a person review the corpus. The separate MiniShop capture track is documented in `CAPTURE_DATA_CARD.md`; its short smoke run is not a scored episode.

## Executable AI-reviewed template comparison

The user-authorized AI review is explicit in every scored artifact. `scripts.evaluation.prepare` imports only public sources into an isolated evaluation database. `scripts.evaluation.resolve_review` checks actual span/document bytes, scope and cutoff. `scripts.evaluation.benchmark` runs five systems with identical eligible corpora and a support-first limit of 12 evidence items / 1,800 MiniLM tokens. Dense and hybrid candidate formation uses literal service mentions, without graph neighbor candidates or graph scoring features.

Two separate tracks are produced: the reviewed question and a shared canonical service-investigation question for each episode. Retrieval relevance applies only to the reviewed question; canonical questions are used for the paired ranking ablations. Anomaly, dense, hybrid and graph systems use the same production template. This does not measure Ollama generation. Ranking metrics include full candidate lists even when the system abstains; exposed-hit metrics and answer coverage are reported separately.

Frozen invocation:

```bash
.venv/bin/python -m scripts.evaluation.benchmark
```

The manifest freezes code/config/model/review/index/lock/prompt hashes before scoring, and resume refuses changed identities. The output root contains per-run predictions, scored predictions, JSON/CSV metrics, paired episode intervals and an error worksheet. Timings exclude database preparation and overlap background capture; embedding-cache hits and misses have separate p50/p95 summaries. Three canonical ablations remove documents, retrieval graph expansion, or graph scoring features; heuristic-versus-learned comparisons provide the ranking ablation. The temporal-filter experiment remains separate and pending. No complete Phase 13 acceptance follows from the synthetic template run alone.


### Completed temporal and claim checks

The isolated temporal diagnostic uses the same canonical query and learned model at baseline plus 120 seconds, comparing properly filtered signals/evidence with deliberately unrestricted episode-end data. This changes windows and eligibility jointly. It reports future-context exposure and ranking changes; it does not assign earlier-cutoff target accuracy without separate labels. It lives only in `scripts/evaluation/temporal_ablation.py`.

The stratified claim review selected the lexicographically first test episode in each of six categories for each of five systems: 30 outputs from six distinct episodes. Decisions are explicit AI annotations, not automatic validation grades. `make evaluation-claims` checks complete claim coverage, unchanged cited IDs and reviewer provenance; `make evaluation-results` renders the measured checkpoint. The source/numeric checks and semantic/task judgments remain separate.

Captured preparation uses a public baseline-relative query cutoff of 240 seconds after the closed baseline boundary, fixed before captured test evaluation. `make evaluation-captured-prepare` imports completed public sources only. `make evaluation-captured-smoke` selects two reviewed training episodes; `make evaluation-captured` refuses incomplete or unreviewed 24-episode sets. Captured reports remain separate from synthetic metrics and do not score unannotated retrieval relevance.

### Captured claim sample and aggregate exports

The captured claim sample uses the first declared schedule key in each of five modes and every baseline system for that key. This produces25outputs from five distinct episodes across training/development/test. Selection is independent of scores; it is a purposive claim audit, not held-out generation accuracy. Packets and explicit review decisions have separate files and hashes. Citation presence and semantic support remain separate measures.

When no unique-target cases exist, Hit@k/MRR are undefined. Paired episode answer-coverage intervals still apply, without interpreting higher coverage as better accuracy. Aggregate exports use EvaluationReport with nulls and explicit numerator/denominator keys; they exclude private rows and remain unpublished until the later API lifecycle is implemented.
