# ADR 0015: Compile evidence plans and version the transfer ranker

Date: 2026-10-12. Status: accepted for the local operator runtime.

## Context

The owner requested architectural improvements to local generation and learned
ranking. The previous generator copied a complete narrative, including fixed
facts and citations. Even schema-valid copying could alter protected fields and
trigger the validator. The frozen synthetic-only ranker had captured development
Hit@1 of 0/5. Preserve that model and its evaluation as historical evidence.

## Decision: constrained selection, independent compilation

Production Qwen receives the question and a catalog of already verified claims.
It emits only `focus` and up to three catalog fact IDs. A request-specific JSON
Schema constrains available focuses and IDs. Independent Python validation checks
membership, uniqueness and focus compatibility; a deterministic compiler supplies
all prose, values and citations. The final source validator still resolves actual
observations, directed paths, cutoff and scope. The model cannot emit authoritative
numbers, edit ranks, execute tools or create graph relationships.

This is model-guided fact selection with server rendering, visibly identified in
the UI. It is not free-form model authorship. Question understanding may still be
wrong, and accurate sources do not prove causation. The bounded English request
policy is a routing convenience; factual integrity does not depend on matching
prompt-injection keywords. Raw documents/logs are absent from the plan input.

Retain the pinned Qwen digest, local allowlist, tokenizer, one-slot durable fence
and shared deadline. Limit output to 192 tokens across both attempts; allow one
targeted repair without reflecting rejected text. Transport uncertainty is never
automatically retried. Actual provider failures still produce a visibly labeled
template; hiding failures would reduce reliability. Cache identity includes the
plan policy and prompt hash. Saved historical results retain their original bytes.

The old full-narrative provider is available only to explicit offline comparisons.
Schema transport follows [Ollama structured-output documentation](https://github.com/ollama/ollama/blob/main/docs/capabilities/structured-outputs.mdx).

## Decision: separately versioned transfer experiment

Candidate-logistic-v4 adds four telemetry features: anomaly excess over observed
callees and positive anomaly shares across measured services. The first eleven
features and the v3 loader remain compatible. Training uses 40 synthetic and ten
reviewed captured training episodes, totaling 270 candidate rows. Captured repeats
of one intervention family share a fold. Names, intervention controls and labels
never enter runtime features; labels are used only by the offline trainer.

Fit preprocessing on training folds only. Select regularization on the minimum
development-origin Hit@1, then mean-origin MRR, grouped training CV MRR and smaller
C. Calibrate rejection separately for the declared synthetic/captured origins;
unknown origins use the global threshold. These are development-selected results,
not new held-out test claims. Origin calibration cannot establish production safety.

Keep heuristic as the application default. V4 improves captured development from
0/5 to 5/5 but reduces synthetic development from 10/10 to 8/10. The model's stored
`selected_default` describes its comparison against the heuristic on development;
it does not override the application default. The new artifact is bundled and
registered for explicit learned selection; frozen v3 remains reproducible.

## Prerequisite repair: snapshot history

The deployed captured-case check exposed missing observation support at an earlier
cursor: import materialization retained baseline and final-window buckets only.
API imports now request intervening buckets before publishing the immutable
snapshot. Existing feature windows, observation identities, old snapshots and
offline evaluation defaults remain intact. Queries still exclude observations and
supporting spans beyond the requested cutoff. No future aggregate substitutes for
missing historical evidence. Existing snapshots require an explicit new import or
snapshot publication to gain history; old saved reports are not rewritten.

## Consequences and evidence

The mechanism eliminates model-authored factual mutation in successful plan
responses. It does not guarantee model availability, correct focus or useful
diagnosis. More captured training families and independent prospective evaluation
are still required. See `phase-reports/reliability.md` for successful and rejected
experiments, actual Docker calls, regression gates and measured tradeoffs.
