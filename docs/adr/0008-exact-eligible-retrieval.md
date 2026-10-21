# ADR 0008 — exact eligible retrieval as the reference

Status: accepted, 2026-10-11.

The first retrieval implementation reads the published snapshot and authoritative source records at the requested cutoff before building a text corpus. Exact TF-IDF and cosine rank only that corpus. A global text or vector index would require additional proof that future and cross-snapshot items cannot enter results, so it is deferred. This makes the lexical and dense baselines use the same observations and runbooks as graph expansion.

The pinned MiniLM revision and tokenizer SHA identify embedding semantics. A model or tokenizer change needs a new cache version and an explicit evaluation. Cached vectors are derived data; source artifacts, PostgreSQL and the graph snapshot remain the evidence authority. The backend image includes the pinned CPU model for offline runtime use. Graph expansion is bounded and discloses truncation instead of silently claiming a complete neighborhood.
