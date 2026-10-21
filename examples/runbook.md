# Payment request investigation

Authored demonstration runbook. Published: 2025-12-01T00:00:00Z.

When checkout reports payment-request errors, inspect both caller-observed dependency spans and payment SERVER spans. A failed call may originate in caller configuration, network transport or the callee. Do not treat a CALLS edge as causal proof.

Approved read-only checks: compare caller and server durations for the same trace; inspect explicit HTTP status/error fields; verify parent completion; compare against a complete eligible baseline. This note is generic and does not label the current episode.
