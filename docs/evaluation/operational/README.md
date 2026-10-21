# Operational evaluation evidence

This directory preserves the declared controlled diagnostic and its implementation
freeze. It is documentation for offline evaluation, excluded from the runtime
image's copied source/data paths. Expected intervention controls are never inputs
to the operational API or its reasoning functions.

| Artifact | What it records |
|---|---|
| [protocol.json](protocol.json) | Capture design, applications, timing and metrics, declared during capture before reasoning outputs |
| [reasoning-protocol.json](reasoning-protocol.json) | Review policy, same-evidence baselines and ablations |
| [reasoning-freeze-final.json](reasoning-freeze-final.json) | Exact implementation hashes frozen before the first operational API evaluation |
| [labels-manifest.json](labels-manifest.json) | Published scoring label hashes and original private receipt hashes |
| [labels/](labels/) | Minimal offline labels: source hash, target event identity, analysis cutoff and intervention type |
| [first-run-failure.json](first-run-failure.json) | Original incomplete run: eight completed queries, then an HTTP 503 |
| [verification-repair.json](verification-repair.json) | Equivalent source-verification query repair, hashes, regression evidence and unchanged reasoning policy |
| [frozen-inputs.json](frozen-inputs.json) | Exact 12 replay cutoffs, scoring label hashes and implementation hashes used in the completed run |
| [results.json](results.json) | Completed five-method API comparison, all per-case scores and 1,021 citation response hashes |
| [large-case-retrieval-repair.md](large-case-retrieval-repair.md) | The second incomplete run, explicit support limit and indexed source lookup repair |

The completed run covers six changed and six pre-change cutoffs. Graph + temporal
reasoning selected the reference event first in 6/6 changed cases and abstained in
6/6 pre-change cases; removing graph paths reduced first-place selection to 2/6.
Removing the timing condition remained at 6/6, so this corpus does not demonstrate
incremental ranking benefit from timing. All 1,021 cited source requests resolved,
and no returned operational event was later than the requested cutoff. See the
README for the full method table and denominator caveats.

An intermediate second run reached ten queries, then exposed a strict retrieval
support bound in the largest asynchronous case. The source lookup was made bounded
and index-oriented without changing the frozen ranking policy, evaluator or
labels. The incomplete first and second runs remain under ignored `reports/`
locally; only this completed run is published as the comparison result.

The policy was designed with knowledge of the controlled scenarios. These are
diagnostic comparisons, not held-out generalization results. The frozen files are
copies of the original timestamped declarations, not rewritten descriptions of a
later result. Documentation may subsequently explain implementation repairs; a
historical document hash in a freeze still identifies its original bytes.

The public captured corpus can be replayed from `data/operational-lab`, and its
exact reference identities can be scored with the published minimal labels.
No commands, local absolute paths or raw control logs are published. Sanitization
changes each label file's bytes and SHA-256; the manifest preserves both that hash
and the original private receipt hash used by the first frozen run. The target
identity and public trace hash remain identical. The original evaluation inputs
are retained unchanged.

The capture script can generate a new corpus and corresponding private receipts;
its actual timestamps and identities will differ. The evaluator accepts explicit
`--index`, `--truth-root`, `--protocol` and a new `--output` directory.
`docs/evaluation` is excluded in `.dockerignore`; the runtime Dockerfile also
copies no evaluation documentation or label paths.

See [reasoning and reproduction](../OPERATIONAL_REASONING.md) for the complete
procedure and limits.
