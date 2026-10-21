# ADR 0013: Final audit defaults and citation byte offsets

Date: 2026-10-12. Status: accepted locally.

The final audit found the CLI's learned default contradicted the captured transfer
results and existing API/UI heuristic default. All entry points now default to
heuristic; explicit learned comparisons remain available. The frozen model and
benchmark outputs are preserved.

The audit also found observation evidence paired a normalized JSONL artifact hash
with an original OTLP byte offset. Identity-based source resolution and numeric
checks succeeded, but the advertised byte pointer did not identify that record.
This was a real provenance defect, missed by earlier metadata comparison tests.

Template v7 computes offsets from the exact normalized artifact bytes. A bounded,
request-owned index consumes the full hash-verified artifact and verifies span
identity, workspace, episode, original source offset, event cutoff and availability
when applicable. Publication rechecks the position and all exposed span references
against scoped support. Integration tests independently seek the named artifact.
No database migration or response contract expansion is required.

The template version changes cache identity, so fresh investigations cannot reuse
v6 output. Saved reports and frozen research retain their original bytes. The UI
marks pre-v7 reports with a historical-offset notice; opening evidence resolves
its current scoped location. CLI evidence also returns the saved locator and an
explicit repair flag. Graph/support metadata named `source_record_offset` still
means the original OTLP offset; `Evidence.locator.record_offset` now means the
normalized artifact byte offset. Normalized records retain the original hash and
OTLP location for lineage. Historical evaluations are not rerun or upgraded into
v7 results, and source validity is not semantic entailment.
