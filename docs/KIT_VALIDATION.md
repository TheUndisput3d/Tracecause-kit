# Planning-kit validation record

Run date: 10 October 2026. These are static checks of the planning package, not application implementation or runtime verification.

- PASS: Nine JSON Schemas pass Draft 2020-12 schema validation.
- PASS: Every JSON file parses.
- PASS: All YAML files parse, including Compose references and configuration.
- PASS: Four example envelopes validate against matching schemas with format checking.
- PASS: Manifest source byte lengths and SHA-256 hashes match included example files.
- PASS: Three synthetic OTLP spans have valid nonzero hex IDs, durations and included parent references.
- PASS: All 20 ordered phase prompts exist and include implementation, verification and completion gates.
- PASS: All 26 cited source IDs resolve to the 29-entry primary-source register.
- PASS: Runtime explanation prompt matches the normative AI specification.
- PASS: Included PostgreSQL initialization shell script passes shell syntax validation.
- PASS: No empty deliverable files or redistributed font files.

## Not executed

Docker Compose CLI validation, image builds, running databases, importing through implemented code, model downloads/inference, model training, captured telemetry, browser/application tests, performance tests, security tests, backup/restore and public deployment have not been executed. The application source and command handlers are intentionally assigned to the implementation phases. Docker is not installed in this authoring environment. YAML parsing and shell syntax validation do not prove a container stack will run.

The included examples are newly authored synthetic contract material. No measured application result, trained artifact, production trace or deployment URL is claimed.
