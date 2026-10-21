# Current limitations

## Scientific scope

- No production incidents, independent human review or user-utility study. User-
  authorized AI review is explicit; shared author/reviewer bias remains.
- Synthetic fixtures have narrow mechanisms/topologies. Their test labels were
  already opened before the final reviewed-question analysis. No new untouched
  test claim is made; the separate v4 experiment uses training/development only.
- Frozen v3 captured development candidate Hit@1 was0/5 and answer coverage0/6.
  V4 now reaches5/5 and5/6 on that development set, but synthetic development
  declines from10/10 to8/10. Development selected the features/settings, ten
  captured training runs cover only two fault families, and the five identifiable
  captured development runs cover one family. Keep heuristic as application default.
  Captured test unique-target ranking is undefined (five ambiguous plus one healthy
  episode). Keep candidate scores, exposed hypotheses and coverage separate.
- Numeric/source correctness does not establish semantic support. Frozen captured
  claim review found8unsupported/13partial among90 claims. Templatev6 fixes specific
  defects without replacing the historical benchmark or establishing new accuracy.
- Actual arrival times and independent exporter-drop counters were not collected.
  Available-time replay is refused; normal parent/count audits do not prove no loss.
- The healthy synthetic fixture may trigger the anomaly rule at its final cutoff.
  The demo uses an explicitly quiet earlier cursor, with this distinction visible.
- The original MiniShop raw/private review artifacts remain local ignored files. Sanitized
  aggregates and the capture procedure are tracked; a clean clone cannot recreate
  the original timestamped bytes without the retained bundles.
- The newer operational lab publishes six actual local trace/receipt bundles across
  three topologies. There are only two incidents per application, one physical
  host, authored ownership assignments and controlled interventions. This is a
  reproducible engineering corpus, not independent production generalization.
- Operational change order uses bounded directed paths and latency/error onset
  buckets. Concurrent faults, absent edges and ten-second timing ambiguity can
  defeat its downstream-degradation heuristic. Historical similarity is an
  explicit weighted overlap, not a learned causal probability.
- Operational runbooks need explicit namespace/environment scope. The bundled new
  captures have no matching authored runbooks; unrelated MiniShop documents are
  excluded. Deployment/history results are deterministic and do not expand the
  optional Qwen investigation fact catalog.

## Operational scope

- Public hosting, external exposure scans and independently trusted public TLS are
  not deployed. Only loopback HTTP and local-CA TLS were verified.
- Historical full-narrative generation took173.4s on the pinned four-CPU container.
  The current evidence-plan protocol uses much shorter outputs:33/33 final local
  diagnostic calls passed on the first attempt with the expected focus. The seven-
  call historical comparison also passed, so this does not establish a measured
  failure-rate reduction. Load, timeouts and question relevance can still vary.
- An uncertain native-provider call stays fenced until operator-reviewed recovery.
  Dedicated Compose restart reconciliation is implemented; deleting a lock is unsafe.
- Restore of historical snapshots requires validated source membership. Backups
  refuse pending jobs/missing hashes; staging/model caches are not backup authority.
- Automatic image rollback is restricted to the same SQL revision. Destructive
  migrations require a reviewed restore/reconciliation procedure.
- Two frontend chunks exceed500kB after minification. Builds pass but bundle size
  and slow-device loading are future optimization work.
- Performance results use the actual24-thread developer host, not a4-vCPU/8GiB VM.
  Container caps and local tolerances are not a production SLA.
- GPU deployment, external log/metric ingestion, automatic remediation, Astronomy
  Shop, ANN acceleration and model document extraction are not implemented claims.

## Historical failures

Failed/interrupted captures, benchmark files and build/test logs are preserved in
phase reports. The missing web-document build input, inert CLI seed invocation,
Caddy file-capability issue and missing clean-fixture migration were corrected and
verified. These are historical failures, not current successful-path placeholders.

## Historical citation offsets

The phase19 audit found pre-v7 observation citations combined a normalized-file
hash with an original OTLP byte offset. Runtime v7 resolves exact normalized byte
positions and validates scope/span/time. Historical reports are preserved and
explicitly marked; open their citations for current resolved locations. Earlier
source-resolution scores did not test this pointer pairing and are not evidence
that legacy byte offsets were correct. See ADR0013.

## Local model output variability

New runtime requests use a constrained fact-selection plan and deterministic
compiler, so the model cannot author or change values, citations or ranks. Strict
source validation remains. The UI identifies this limited model role. This avoids
the full-report copying mechanism below but does not guarantee model availability,
correct fact selection or diagnostic usefulness. Invalid plans/provider failures
remain visible; successful rendering is not a production reliability estimate.

The phase19 native Ollama0.18.3 browser check received a schema-valid narrative
that failed fixed-support validation and correctly fell back to a template. The
same request subsequently passed without code/prompt changes (1648 input and598
output tokens,21.39s). The first rejected narrative is not retained, so its exact
changed field is unknown. A successful retry does not erase the failed attempt or
establish a first-attempt reliability rate. Keep the strict validator and visible
fallback; template mode remains available. This native diagnostic is separate
from the pinned0.40.2 container's phase18 result.
