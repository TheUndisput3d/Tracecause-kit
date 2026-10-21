# Independent MiniShop label review

**User update, 11 October 2026:** Codex is authorized to perform the reviews and proceed. Use explicit `review_origin=ai`; independent human review was not performed. This supersedes the human-only gate below; see ADR 0010.

Phase 12 requires representative human review. Phase 13 also requires all captured test labels reviewed before benchmark scoring. Automated effect checks and this implementation agent's inspection cannot substitute for those attestations.

## Prepare the current evidence

Run `make capture-review-packet` from the repository root. It refreshes the private audit, preserves existing human worksheet entries, and renders `reports/capture/review_packet.md`. It does not change labels or control the active capture. Newly completed episodes are added on the next refresh; a partial packet cannot establish complete protocol coverage.

Open the packet, its linked public manifest/OTLP file, and each private `truth.json`. Check source hashes, actual stage intervals, achieved load, reset status and parent-linked service calls. The packet reports server spans by their start time within each actual stage; these are descriptive stage measurements, distinct from the application's fixed ten-second signal buckets.

## Review each selected episode

1. Confirm that span resource names and client/server parent relationships support the five-role mapping and observed calls.
2. Compare baseline, intervention and recovery errors/latency against the actual control/load record. Confirm injection separately from whether a cause is identifiable from public telemetry.
3. Choose accepted target services using the observed evidence. In caller-timeout cases, a payment timeout alone cannot establish a payment fault: checkout changed its timeout while payment remained healthy. Mark ambiguity explicitly. Retain unobservable runs and explain the limitation.
4. Fill the human columns below. Leave the first six machine columns unchanged. Do not approve automatically proposed targets without reviewing their support.

| Column | Required content for a reviewed row |
|---|---|
| `service_mapping_confirmed` | `yes` after checking mapping |
| `intervention_confirmed` | `yes`, or `not_applicable` for a healthy control |
| `accepted_targets` | Semicolon-separated MiniShop service names; blank for healthy controls |
| `identifiable` | `yes` or `no`; healthy controls use `no` |
| `reviewer` | Actual human reviewer's identity |
| `review_date` | Actual review date as YYYY-MM-DD |
| `independent_review_attestation` | `yes` only after independent human review |
| `evidence_notes` | Source hash, trace/span identifiers or exact record locations and the reasoning for the decision |
| `disagreement_notes` | Disagreement with proposals or another reviewer, including unresolved uncertainty |

The file is `reports/capture/review_sheet.csv`. Blank rows remain unreviewed. Do not create fictional reviewer identities or dates. Keep all private fault information outside application runtime and public manifests.

## Validate and retain the audit

- `make capture-review-validate` reports current coverage, including partial review.
- `make capture-review-gate` requires all 24 captures with no automatic quality issues and reviewed representatives of all five modes and all three splits.
- `uv run --locked python -m scripts.capture.review --require-representative --require-all-test` additionally requires every captured test row reviewed.

Validation records audit/review hashes and accepted labels in `reports/capture/review_validation.json`. It checks self-reported attestations, not human identity. Any quality issue, unobservable effect or unresolved dispute remains reportable; a valid CSV alone is not proof of label correctness.

## Integrity checks before review

The audit verifies the frozen schedule hash and stage durations, typed manifest identity, source size/hash, baseline and event intervals, and recomputed span-quality counts. Stage times are compared using integer nanoseconds; overlaps and reversed intervals fail validation. A conflicting worksheet leaves the previous audit and worksheet intact. The review importer checks the audit schedule hash and actual run-key coverage: reviewing a partial set of test captures cannot satisfy the all-test gate.

## Authorized AI-review commands

The user delegated review to Codex; see ADR 0010. The AI sheet is separate from the original human sheet. It uses `ai_reviewed` in the attestation field and never claims independent human review.

```bash
make capture-review-packet capture-review-evidence
make capture-ai-review-validate
# Only after actual review of representative modes/splits and all test captures:
make capture-ai-review-gate
```

Evidence extraction produces source examples only. Review each new capture's stage statistics, parent-backed calls and source examples before recording a decision. Do not approve future captures automatically.
