# ADR 0010: User-authorized AI review of evaluation evidence

Date: 2026-10-11. Status: accepted by direct user instruction.

## Authority

The user instructed: "u review it, i can't i dont understand, u review and implement and make a decision further". This replaces the earlier requirement to wait for independent human label/question/claim review. Codex will review the evidence, record decisions and continue implementation. GitHub pushes still require explicit permission.

## Implementation

- Identify the reviewer as Codex and the review origin as `ai`. Never populate an independent human attestation for an AI review.
- Preserve the original human-review workflow; provide an explicit AI-review mode and separate artifacts. Reports state which origin was used and that independent human validation was not performed.
- Inspect source-linked observations before choosing accepted targets. Injection intent alone is not an accepted diagnosis. Keep healthy, ambiguous and unobservable cases explicit.
- Question answerability and fault identifiability are separate decisions. Evidence grades must resolve to actual source records. Model outputs are not their own gold labels.
- Keep model, split, corpus and protocol frozen. Reviewing labels does not authorize tuning on test data, shortening captures or inventing results.
- Phase acceptance may use the user-authorized AI review. Unfinished collection/experiments remain unfinished. No independent-human-reviewed or production-quality claim follows from this decision.

## Limitations

The implementation author and reviewer are the same AI assistant. This is not independent adjudication and may share implementation biases. Results must expose this limitation in data/model cards and benchmark reports.
