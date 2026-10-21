# Large asynchronous case: bounded source lookup repair

The second frozen API evaluation completed ten of twelve queries and resolved
807 source citations. It then returned HTTP 409 on the changed cutoff of the
second Media Works episode. The strict graph reader reported that the eligible
observation support set exceeded its former 100,000-reference limit. This was an
explicit refusal, not an incorrect ranking result. The incomplete output remains
under `reports/operational-lab/evaluation-v2` locally and is excluded from the
published aggregate.

The support bound was raised to 128,000 with an SQL `LIMIT (bound + 1)` so an
oversized scope still fails explicitly. A subsequent direct request exposed a
separate PostgreSQL statement timeout from a large tuple-`IN` source lookup.
Both observation-source and graph-call source reads now group eligible trace IDs
into bounded indexed batches, retain exact `(trace_id, span_id)` matching in
memory, and cap materialized rows. The reader still checks workspace, episode,
snapshot membership, source presence and time eligibility. A scope beyond those
explicit limits is rejected rather than silently sampled.

After the repair, the previously failing Media Works changed-cutoff operational
API request returned HTTP 200 in 13.72 seconds. The third frozen evaluation
completed all twelve queries and verified 1,021 cited source responses. The
ranking policy, evaluator, captured data and reference event IDs were unchanged:

| Component | SHA-256 in completed evaluation |
|---|---|
| Reasoning policy | `6db208a4e90a2983b5021ecf782778abba23a9fc1b799a8789ab4228e31654db` |
| Evaluator | `8366f13fae104468f61e0d2bc0cb65624329c4833043ca423f06cee1847ec619` |
| Repaired graph query reader | `8d47c15ed1e67dc358c4e1c4135467b9e19edfb23ccc2a2560995f5ba0f038f4` |

The current implementation is still bounded. It does not claim unlimited graph
scale or a latency guarantee on a smaller machine. The completed result is
published in [`results.json`](results.json); the incomplete runs are retained as
engineering failures rather than counted as successful evaluations.
