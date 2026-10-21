# Operational reasoning: declared diagnostic protocol

This extension evaluates deterministic graph queries over **actual controlled
localhost captures**. It does not establish production diagnosis accuracy. The
applications and intervention design were known while designing the algorithm;
these six episodes are not a held-out generalization benchmark.

## Runtime rules

1. A deployment or configuration receipt must be source verified, belong to the
   requested immutable snapshot, and precede the replay cutoff. Available-time
   replay also requires its receipt to have arrived by that cutoff.
2. Follow at most three directed dependency steps from the selected symptom
   service. `CALLS` retains the existing HTTP span-parent rule. `MESSAGE_FLOW`
   requires a consumer's direct parent to be a producer in the same trace, on a
   different service, with a nonnegative delay of at most one hour. Async spans
   need not overlap. No broker or delivery guarantee is inferred.
3. An observed degradation requires latency or error deviation of at least 3,
   with at least six populated baseline buckets and ten baseline requests.
   Latency/error onset is recomputed from the exact eligible ten-second buckets
   using the existing version-1 median/MAD formula and floors of 5 ms and 0.01.
   Volume change alone is context, not degradation. This matters because the
   legacy volume feature includes zero-filled baseline buckets before these short
   captures began. The original ranker and research feature semantics are retained.
4. A change strictly before the first degraded bucket is a review lead. A change
   inside that bucket has uncertain ordering. Later changes, healthy services,
   missing onsets and disconnected changes remain visible with explicit status.
5. Within prior changes, a service with no further observed degraded dependencies
   is reviewed before upstream services with degraded descendants. Each descendant
   has an inspectable path, measurement and baseline sources. Recency and path
   length break remaining ties. This **downstream degradation heuristic is not
   causal proof**: concurrent faults, incomplete topology and noisy measurements
   can defeat it. Contradicting changes are retained.

Every async projection read checks its complete scoped relationship structure,
including orphan and cross-scope edges, then validates eligible observation
content against the normalized source artifacts and authoritative span rows.
The response exposes a maximum of 24 changes and eight descendant proofs per
change; the complete descendant count is retained. An incomplete graph is marked.

## Historical comparisons

Only completed episodes before the current episode, in the same workspace,
application and environment, qualify. Snapshot membership pins the historical
snapshots. Similarity is an explicit weighted Jaccard overlap: services 20%, typed
directed dependencies 40%, observed anomaly kinds 40%. Empty components are
omitted and the remaining weights normalized. The score is neither confidence nor
causal probability.

Latest observed version, configuration, host and ownership values are compared.
An absent value means **unknown**, not removed or unchanged. Both sides carry an
episode, snapshot, source identifier and replay cutoff. Available-time history
has two clocks: the earlier incident's event-time view is eligible only when all
of its records were known by the current knowledge cutoff. Citation metadata
includes the event replay mode and that knowledge cutoff explicitly.

There are only two captured episodes per application here. Therefore historical
lookup demonstrates retrieval, exclusion and inspectable differences; it does
not measure search quality across a large incident archive.

## Authored document scope

New operational snapshots require an explicit source declaration before including
an authored runbook. Matching a short service name alone is insufficient: an old
MiniShop `checkout` runbook does not belong to Parcel Shop merely because both
applications use that name. A runbook can declare its actual service namespace
and environment in the immutable Markdown front matter:

```yaml
service_scope:
  namespace: shop-chain
  environment: operational-lab
```

Canonical service mentions and curated aliases are then resolved only within
that exact scope. An unmatched document is excluded; a pinned document that
violates its snapshot's scope policy is rejected. Existing snapshots retain
their recorded document policy so the historical research corpus and hashes do
not change. Scope is an authored declaration, not something inferred by an LLM.
These captures currently contain no such scoped runbooks, so their operational
graphs do not inherit unrelated MiniShop document evidence.

## Evaluation design

The capture protocol was written before reasoning output was inspected, under
`reports/operational-lab/protocol.json`. It declares three tiny applications
(synchronous chain, parallel fanout and asynchronous queue), two interventions
per application (actual release artifact replacement and a runtime policy
change), a newer benign entry-service change, and 70/60/20 seconds of baseline,
changed and recovery workload. Capture controls and expected intervention IDs
remain outside the runtime. Minimal reference labels are published separately in
[`operational/labels`](operational/labels), with their original private receipt
hashes preserved in the label manifest. The API receives only episode/snapshot,
cutoff and symptom identifiers.

`scripts/evaluation/operational_capture.py` runs twelve real API queries: each
business entry at the changed-window cutoff and just before the target change.
It retains complete responses, resolves source citations and measures:

- Target event candidate recall, separately from review-priority recall.
- Top-1 event and service identity, and event recall at three, on six change queries.
- Abstention on six pre-change replay queries. Their cursors may precede the
  analysis interval, leaving no eligible analysis signal; this is not a healthy
  false-positive-rate estimate.
- Future-event exclusion, exact scoped citation resolution and historical state differences.

Every method sees the same complete eligible change pool, capped at 24, and emits
at most three results. The comparison refuses a truncated pool. Methods are:

| Method | Decision rule |
|---|---|
| Graph and time | Source-verified temporal rule and downstream degradation heuristic |
| Chronological | Most recent eligible change first |
| Direct service | Most recent change on the selected symptom service |
| Without graph | Prior latency/error-degraded changes ordered by recency, ignoring reachability and descendants |
| Without time | The same dependency reachability and downstream degradation order, ignoring onset ordering |

These are controlled diagnostic comparisons, not trained competitors or claims
about state-of-the-art incident diagnosis. No tuning follows the first outputs
under the same declared version. Implementation failures are retained; any later
algorithm change requires a new version and an explicit exploratory label.

## Reproduction

After the complete Docker stack has imported the public captures:

```bash
.venv/bin/python -m scripts.evaluation.operational_capture \
  --base-url http://127.0.0.1:14000 \
  --protocol docs/evaluation/operational/protocol.json \
  --truth-root docs/evaluation/operational/labels \
  --output reports/operational-lab/evaluation-new
```

The published minimal offline labels reproduce scoring for the bundled corpus.
Their hashes differ from the original private receipts used in the first run;
`operational/labels-manifest.json` preserves both identities. These labels and the
evaluation documentation are excluded from the Docker build context and runtime.
For new controls, capture into new directories and pass that public index and its
private truth path. New captures have new actual timestamps and identities. Never
overwrite a previous capture or report.
