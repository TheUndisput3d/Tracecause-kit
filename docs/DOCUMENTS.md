# Document ingestion and exact citations

`data/runbooks/demo` is a frozen authored corpus. Its Markdown files begin with YAML front matter: stable `source_id`, `version`, `title`, `origin`, `document_type`, UTC `published_at`, `license`, `attribution` and optional `curated_service_refs` with namespace, environment, canonical name and alias. The parser rejects unknown fields, unsafe paths, malformed timestamps, oversized files and hash mismatches. Unknown publication time is allowed by the parser but is excluded from snapshot projection until a trustworthy timestamp is supplied.

Import the corpus into PostgreSQL and the same immutable artifact root used by the episode:

```bash
tracecause documents ingest --corpus-dir data/runbooks/demo --artifact-root /absolute/artifact/root --password-file .secrets/postgres_password
tracecause graph build --episode-id <uuid> --artifact-root /absolute/artifact/root --password-file .secrets/postgres_password --neo4j-auth-file .secrets/neo4j_auth
tracecause documents review --episode-id <uuid> --artifact-root /absolute/artifact/root --password-file .secrets/postgres_password
tracecause graph chunks --episode-id <uuid> --snapshot-id <uuid> --as-of-ns <epoch-ns> --artifact-root /absolute/artifact/root --password-file .secrets/postgres_password --neo4j-auth-file .secrets/neo4j_auth
```

Ingestion is idempotent for a source ID/version/hash and rejects changed bytes at the same source ID/version. PostgreSQL stores document/chunk identity, source artifact hash, publication time and exact character offsets. The source bytes remain in the content-addressed artifact store; the graph is a rebuildable projection. A new graph snapshot is required after import. `--document-cutoff-ns` on `graph build` can include later published documents in a new snapshot. A read with an earlier cutoff still excludes them. Source-available replay additionally requires trustworthy source and document availability timestamps; these authored and synthetic inputs lack them, so that mode refuses the read.

Chunking is heading-aware. The 180-token budget includes title/heading context, and adjacent chunks overlap by about 30 actual tokenizer word pieces. The model tokenizer's built-in 128-token truncation and padding are disabled before measurement; the effective encoder limit is 256. Character offsets always refer to the original Unicode Markdown source and are checked against its SHA-256 artifact. No text is silently truncated. Text and exact offsets are stored on `Chunk` nodes for bounded graph reads and checked again against PostgreSQL and artifact bytes.

The deterministic extractor recognizes authored `**Symptom:**`, `**Check:**` and historical `**Historical report:**` lines. It creates only `DESCRIBES_SYMPTOM`, `SUGGESTS_CHECK` and `REPORTS_HISTORICAL_CAUSE` assertions, with exact quote offsets, extractor version and verification status. Historical reports cannot create a telemetry `CALLS` edge or a current `CAUSES` fact. Only accepted, scope-resolved mentions create `MENTIONS` or assertion `ABOUT_SERVICE` links. Ambiguous and fuzzy mentions remain in the review report. The extractor interface and quote validator are reusable for a later local-model extractor, but this phase does not run one.
