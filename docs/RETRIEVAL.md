# Evidence retrieval

Phase 08 retrieves evidence from one explicit READY graph snapshot at one cutoff. The request carries workspace, episode, snapshot, event or source-available mode, question, optional symptom service and top-k. The graph reader checks PostgreSQL ownership and the Neo4j snapshot marker, then exposes the same eligible document chunks and deterministic observation summaries to lexical, dense, hybrid and graph strategies. New snapshots label both Chunk and Observation nodes `Retrievable`; the exact reference reads still validate their source-specific labels and provenance. Returned items carry a source locator, scope, cutoff, modality scores and stable evidence ID. Retrieval does not assign a cause or generate an explanation.

Run a comparison without generation:

```bash
uv run --locked --group ml tracecause retrieval compare \
  --episode-id <uuid> --snapshot-id <uuid> --as-of-ns <epoch-ns> \
  --question 'Why is checkout timing out?' \
  --artifact-root /absolute/artifact/root \
  --embedding-cache-root /absolute/cache/root \
  --password-file .secrets/postgres_password \
  --neo4j-auth-file .secrets/neo4j_auth
```

The CLI prints separate strategy results and traces. It needs the locally cached model revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` of `sentence-transformers/all-MiniLM-L6-v2`. The backend image builds the pinned CPU ML group and bundles that model cache; runtime inference is offline. The host CLI uses `local_files_only=True` and fails explicitly when the weights are absent. Initial local download can be performed with the pinned `SentenceTransformer` call used in `tests/model/test_minilm.py` before running the command. `make test-embedding-integration` verifies actual 384-dimensional normalized inference.

TF-IDF fits only the eligible corpus at the request cutoff and treats punctuation-bearing service identifiers as terms. Dense retrieval scores every eligible vector with exact cosine. MiniLM encodes batches of 32; normalized vectors are cached under a SHA-256 key of model version, tokenizer hash and text. The cache refuses unsafe entries and validates dimension and normalization. Neither path uses a global future document vocabulary or an approximate index. Empty eligible documents produce a `no_eligible_documents` warning; an empty entire corpus also produces `no_eligible_evidence`.

Hybrid fuses up to 40 results per modality with `1/(60+rank)`, starting rank at one and deduplicating by evidence ID. Graph expansion starts with the same hybrid seeds and optional symptom service. It traverses trace-supported `CALLS` edges at most three hops, retains each edge's direction and qualifying observation support, and stops with `graph_truncated` when the service, edge, evidence, path or traversal budget is reached. Graph support is a ranking signal, not proof of a fault. The candidate cap is 20 services, graph node cap 80, edge cap 120 and supporting evidence cap 60. The exact text corpus cap is 20,000 eligible items; a cap breach fails explicitly.

`data/retrieval/annotations.json` is a small developer-authored draft with 0/1/2 relevance judgments over actual frozen runbook chunk IDs. It is suitable for smoke checks and needs independent review before external retrieval-quality claims. Phase 08 records no measured retrieval improvement. The optional passage reranker and approximate indexes remain disabled.
