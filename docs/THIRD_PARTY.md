# Third-party components

Newly authored application code uses Apache-2.0 (`LICENSE`). Dependencies and images keep their own license terms. This is an inventory for review, not a grant to redistribute their binaries.

| Component | License source / note |
|---|---|
| MiniLM model weights | Apache-2.0 in the [model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) |
| MiniLM tokenizer metadata | Bundled from pinned model revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; same model card license. `tokenizer.json` SHA-256 `be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037` and `sentence_bert_config.json` SHA-256 `fc1993fde0a95c24ec6c022539d41cf6e2f7c9721e5415d6fb6897472a9cd4b7` |
| Qwen3-4B-Instruct-2507 weights | Apache-2.0 in the [model card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) |
| Ollama software | MIT in [repository LICENSE](https://github.com/ollama/ollama/blob/main/LICENSE) |
| Neo4j Community | GPLv3; consult [Neo4j licensing](https://neo4j.com/licensing/) for distribution obligations |
| Docker Engine / Docker Desktop | Separate licensing contexts; see [Engine](https://docs.docker.com/engine/) and [Desktop](https://docs.docker.com/subscription-billing/desktop-license/) |

Resolved package names, versions, and package metadata licenses are generated in `docs/dependency-inventory-python.csv` and `docs/dependency-inventory-npm.csv` when installation completes. Review unknown or ambiguous metadata before public release.
