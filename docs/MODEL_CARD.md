# TraceCause candidate ranker model card

## Current operator runtime: candidate-logistic-v4

The complete Docker stack now registers `models/candidate-logistic-v4/model.json`
for **explicit learned selection**. The application default remains heuristic.
V3 below is preserved as the frozen research reference, not silently replaced.

V4 fits 50 identifiable training episodes: 40 synthetic plus ten captured MiniShop
runs (five payment-error and five inventory-delay). They supply 270 candidate rows.
The captured labels are user-authorized AI review, not independent human labels.
The fifteen raw features add error/latency excess over observed callees and each
service's positive error/latency share. No service name, injected control, future
aggregate or gold target is a runtime feature. The four derived features use zero
for unavailable positive anomalies; the original features retain missing indicators.

Median imputation, scaling and logistic regression remain the architecture. Related
captured runs share a validation group. Training-fold preprocessing remains isolated.
Development selection maximizes the worse origin's Hit@1, then mean-origin MRR,
then grouped train CV MRR; C=10 was selected. Development was also used to explore
these features and thresholds. It is not an independent final evaluation.

| Development result | Frozen v3 | V4 |
|---|---:|---:|
| Captured identifiable candidate Hit@1 | 0/5 | 5/5 |
| Synthetic identifiable candidate Hit@1 | 10/10 | 8/10 |
| Captured threshold answers, correct | 0/6, 0 | 5/6, 5 |
| Synthetic threshold answers, correct | 5/25, 5 | 5/25, 5 |

The separate captured/synthetic development thresholds are 2.6296358973918252 and
3.61701932997257; the latter is also the global fallback for other origins. Neither
score nor threshold is a probability. All five captured development faults belong
to one held-out-from-fitting catalog-error family; this is a very small selection
set. No test evidence or test target was used by the new training/selection run.

Artifact SHA-256: `76b66e66d9c5686eecced3cd82751977f91b961740a3f5521ffe3448cda26811`.
The tracked `models/candidate-logistic-v4/development_summary.json` gives the measured
comparison. `make ranker-register-bundled` now registers v4. To reproduce training:
`make train-transfer TRANSFER_OUTPUT_ROOT=reports/new-transfer-run` with the ML
dependencies, generated synthetic fixtures and retained captured review/prepared
bundles available. The output directory must not exist. Those original timestamped
captured bundles are local artifacts; a clean clone can run bundled inference but
cannot reconstruct the same training bytes without them.

V4 is an experimental option because captured improvement trades off synthetic
accuracy, and more training families/independent evaluation are still needed.
See [ADR0015](adr/0015-evidence-plans-and-transfer-ranker.md) and the
[verification report](phase-reports/reliability.md). The following sections describe
the unchanged **historical v3** model and evaluation.

## Intended use

`candidate-logistic-v3` orders already generated service candidates for an incident investigation. Its `linear_decision_score` is a ranking value, not a calibrated probability or a causal effect. The model cannot invent a candidate, change trace relationships, or establish root cause. It abstains below a threshold selected on the development split; a missing, corrupt or incompatible model visibly falls back to `heuristic-v1`.

## Data and split

The model was fitted on the synthetic fixture `seed-20261010`. The scenario-group split was frozen in Phase 02: 14 train groups (70 episodes), five development groups (25 episodes), and five held-out test groups (25 episodes). The trainer reads only `data/gold/seed-20261010/training_access.json` and its listed train/development truth files. It does not open the held-out test truth or global private index. The access-manifest SHA-256 is `00947953783772c73d83ea0839ba6b50d70b5b476dd7317d59dcad3b8759bfe7`; the full train/development feature-table hash is `a31f4622b444f8bef1ba4fad7aa07f1db0d7268d6314ff058a8bd52e0c092a5f`.

Each feature row represents one `(episode, final event cutoff, candidate service)` pair. Public OTLP/JSONL is normalized with the production parser and redactor. The production `compute_signals` and `candidate_features` functions produce the same feature schema used at runtime. The training table contains 520 candidate rows across the 95 train/development episodes. Only 40 identifiable faulty train episodes (220 rows across eight groups) enter the relevance fit. Healthy and unidentifiable episodes receive no invented positive label; they enter the development rejection analysis. An identifiable episode whose target is absent from candidates counts as a candidate-recall and end-to-end ranking failure, including when it has zero candidate rows.

The ordered raw features are latency anomaly, error excess, volume deviation, onset gap, anomalous-caller fraction, directed distance, incoming degree, outgoing degree, distinct supporting-trace count, completeness and numeric question-to-signal retrieval support. The signal text used for that final overlap removes the service key. Episode IDs, group IDs, service IDs/names, scenario labels, injected target and future aggregates are metadata only, never model inputs. Missing values are preserved for median imputation and missingness indicators.

## Training and selection

The scikit-learn pipeline is `SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True) -> StandardScaler -> LogisticRegression(solver="liblinear")`. Preprocessing is fitted within each training fold. Candidate rows from each eligible episode have weights summing to one; `class_weight=None`. The fixed seed is 20261010. Five group-aware folds on training groups compared `C={0.01, 0.1, 1, 10}` by mean fold MRR. The observed means were 0.8600, 0.9300, 0.9000 and 0.8533; `C=0.1` won. Group-level folds include any identifiable episode with no candidates as a pipeline failure. Fold counts are small and unequal by episode count.

Development is a selection set, not an untouched test set. On its 10 identifiable faulty episodes, candidate recall was 10/10 for the shared candidate generator. The learned scorer achieved Hit@1 10/10, Hit@3 10/10 and MRR 1.00; the heuristic achieved Hit@1 4/10, Hit@3 6/10 and MRR 0.60. Conditional ranking scores equal those end-to-end values because candidate recall was 100% on these 10 episodes. These are measured synthetic development results, not generalization or held-out test claims. The remaining 15 development episodes were healthy or unidentifiable and are outside the unique-target ranking denominator.

The development rejection threshold is `0.10957502750435866` on the linear decision score. It answered five of 25 development episodes (20% coverage), with five correct accepted top candidates and zero false alarms under the development objective; accuracy among those five answers was 100%. This severe abstention limits utility and may not transfer to captured telemetry. Runtime first checks data adequacy/anomaly, then applies this threshold to a learned top score. The heuristic remains available explicitly and is used with a visible warning if the learned artifact cannot be used.

## Artifact and inference

`make train` regenerates the fixtures/access manifest and writes ignored local `reports/phase11-training/{feature_table.jsonl,model.json,development_report.json}`. `make dev-report` prints the measured development comparison. The reviewed, 2.8 KB reference artifact is committed at `models/candidate-logistic-v3/model.json`; `make ranker-register` registers freshly trained local output. Registration hashes the JSON and uses the content-addressed artifact store and PostgreSQL registry. The frozen v3 SHA-256 is `a824ecaef074ec37402b0caa7bf880c92929e57171b973ff2e0e9e72feec277f`.

The JSON records raw/transformed feature order, imputation values and indicator positions, scaler means/scales, coefficients/intercept, training hashes, Python 3.12.3, NumPy 2.5.3, scikit-learn 1.9.1 and development validation. No pickle/joblib loader is used. Runtime checks the registered workspace, artifact hash, model/schema version and selected default. Exported scores matched the scikit-learn decision function within `1e-8` on development rows. A supported learned report attaches standardized feature contributions and eligible observation references to each displayed score; the report validator checks the score/order, exact contribution objects and source eligibility.

## Final evaluation and default decision

The frozen120-episode synthetic and24-episode captured comparisons are complete.
`EVALUATION_RESULTS.md` is generated from their actual artifacts. Five systems use
matched eligible sources/context; candidate ranking and exposed hypotheses remain
separate. Learned canonical synthetic test candidate Hit@1 was10/10 identifiable
episodes, exposed Hit@1 5/10, and answer coverage5/25. On captured development,
learned candidate Hit@1 was0/5, graph heuristic1/5 and anomaly5/5; learned answered
0/6development and0/6test. Captured test target ranking is undefined because all
six cases are ambiguous/healthy. The frozen model was not tuned on these findings.

The earlier artifact's `selected_default=learned` records the phase11 synthetic
development decision. The application/CLI now default to heuristic after captured
transfer failed. Explicit learned selection and the immutable reference artifact
remain available; the old artifact is not rewritten to imply a different training
history. The final CLI-default regression enforces this operational decision.

## Limits and prohibited interpretations

Synthetic mechanisms/topologies and ranking denominators are small. Provisional
synthetic test labels were opened before later AI question review; this is an
already-opened protocol, not a newly untouched test result. All120questions and
24captures received explicit implementation-agent review under user delegation;
no independent human adjudication or production validation was performed.

Captured AI claim review found69supported,13partial and8unsupported structured
claims among90 reviewed claims. Synthetic review found154supported,7partial and
8unsupported among169. Source IDs and numeric checks do not prove semantic
entailment. Runtime templatev6 later restricts service exposure and adds explicit
baseline examples with narrower language. That is a separately versioned
regression repair, not a new held-out quality result.

Features/coefficients are associations, not causal effects. Healthy/ambiguous
coverage, false alarms, missing candidates and abstentions must remain visible.
Independent labels, calibration, wider captured scenarios and drift checks are
future work. Never use this model to trigger automatic remediation.

## Local generator and licensing

The ranker JSON is project-authored Apache-2.0. It is distinct from Apache-2.0
MiniLM retrieval and optional Apache-2.0 Qwen weights. Pinned model/runtime identities
are documented in LOCAL_GENERATION and THIRD_PARTY. Actual pinned Ollama0.40.2
container generation passed in phase18 (1416input/547output tokens,173.4seconds,
no fallback under fourCPU). This verifies a bounded adapter, not language-model
quality or production usefulness. Saved reports retain their original generation
origin and are marked replayed when served.

Final audit runtime repair: template v7 corrects normalized-artifact citation byte
offsets, with a separate cache identity and no model retraining. Historical v6 and
frozen benchmark reports retain their original locators; the UI identifies them
and evidence inspection resolves current offsets. This does not revise frozen
accuracy or semantic claim-review findings. The CLI default is now heuristic,
consistent with the API/UI and the captured negative transfer decision.
