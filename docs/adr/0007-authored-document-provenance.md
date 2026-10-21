# ADR 0007 — authored documents and assertion provenance

Status: accepted for Phase 07 on 2026-10-11.

Keep original authored Markdown bytes in the content-addressed artifact store and document/chunk identity in PostgreSQL. Derive deterministic exact-offset chunks with the pinned MiniLM tokenizer, without tokenizer-file truncation. Project only documents published by an explicit document cutoff into a new graph snapshot; never modify a READY snapshot. Resolve service mentions within the snapshot's canonical service identities, applying only curated aliases automatically and routing ambiguous/fuzzy matches to review. Extract assertions only from explicit authored markers, verify their quotes against original offsets and allowlisted predicates, and represent historical reported causes as document claims rather than telemetry facts. Build lexical/dense retrieval from the eligible chunk corpus in Phase 08; optional Neo4j semantic indexes remain disabled.
