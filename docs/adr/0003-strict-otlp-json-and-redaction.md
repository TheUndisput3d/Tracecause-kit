# ADR 0003 — strict OTLP JSON adapter and conservative redaction

Date: 2026-10-10. Status: accepted for Phase 03.

The importer accepts standard OTLP JSON export requests with hex IDs and integer enums, in a single JSON object or one request per NDJSON line. This follows the [OpenTelemetry OTLP JSON mapping](https://opentelemetry.io/docs/specs/otlp/) and is checked against the pinned upstream example in `data/fixtures/official/`. A named Collector file adapter exists but rejects input until a pinned exporter capture is available. This avoids guessing whether a future Collector output uses standard OTLP JSON or another encoding.

The first redaction policy is allowlist-first. It keeps selected typed status/method/error-category fields, reviewed MiniShop route names and scoped service identities; other span attributes and dynamic operations are removed. Unsafe service names remain unresolved. Source bytes remain untouched in their input location, while the CLI emits only redacted JSONL and separate original/redacted hashes under an artifact root. Conflicting duplicate span keys are removed from normalized output; one earlier accepted copy is not silently retained.

This policy can discard useful telemetry and may fail to recognize novel personal-data patterns. Future changes require a new redaction version, migration review and adversarial tests. Persistence and original-source retention policy are Phase 04 decisions; this ADR does not claim a captured Collector conformance run.
