# Primary source register

Reviewed 10 October 2026. All project capacities, thresholds, engineering-effort estimates and quality goals are proposed design decisions, not measured outcomes. Exact packages, image digests and model revisions must be resolved against these primary sources during phase 00. Source IDs are stable and intentionally not contiguous.

## S01 - OpenTelemetry demo feature flags

https://opentelemetry.io/docs/demo/feature-flags/

Decision supported: Optional Astronomy Shop fault controls; verify exact names/types against a pinned demo revision.

## S02 - OpenTelemetry demo architecture

https://opentelemetry.io/docs/demo/architecture/

Decision supported: Optional larger multi-service telemetry source, separate from the project-owned MiniShop.

## S03 - OTLP specification

https://opentelemetry.io/docs/specs/otlp/

Decision supported: OTLP/JSON envelope, hexadecimal trace/span IDs and integer serialization; preserve timestamp precision.

## S04 - OpenTelemetry demo license

https://github.com/open-telemetry/opentelemetry-demo/blob/main/LICENSE

Decision supported: Repository Apache-2.0 license; review separate third-party notices and externally linked material.

## S05 - Neo4j vector indexes

https://neo4j.com/docs/cypher-manual/current/indexes/search-indexes/vector-indexes/

Decision supported: Optional version-dependent acceleration; the project defaults to exact eligible-corpus ranking.

## S07 - MiniLM model card

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

Decision supported: 384-dimensional embeddings, default 256-word-piece truncation and Apache-2.0 model license. Pin a revision.

## S08 - Sentence Transformers cross-encoders

https://www.sbert.net/docs/cross_encoder/pretrained_models.html

Decision supported: Optional text reranking; not the same task as the trained candidate-service scorer.

## S09 - scikit-learn common pitfalls

https://scikit-learn.org/stable/common_pitfalls.html

Decision supported: Train-only preprocessing and leakage prevention using fitted pipelines.

## S10 - scikit-learn GroupKFold

https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html

Decision supported: Non-overlapping groups in cross-validation; project-specific grouping still requires domain judgment.

## S12 - OpenTelemetry sensitive-data handling

https://opentelemetry.io/docs/security/handling-sensitive-data/

Decision supported: Attribute minimization, sanitization and explicit handling of sensitive telemetry.

## S13 - OpenTelemetry demo Docker deployment

https://opentelemetry.io/docs/demo/docker-deployment/

Decision supported: Independent prerequisites/resources for the optional larger capture source.

## S14 - Codex repository instructions

https://developers.openai.com/codex/guides/agents-md

Decision supported: Persistent AGENTS.md instructions. Official documentation may redirect to ChatGPT Learn.

## S15 - Codex development workflows

https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex.md

Decision supported: Context files, execution plans and evidence-based iterative implementation. This kit's phase filenames are project conventions.

## S17 - Qwen3-4B-Instruct-2507 model card

https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507

Decision supported: Apache-2.0 open weights and non-thinking instruct variant. This does not reproduce the pretraining data/pipeline.

## S18 - Exact quantized Ollama model tag

https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M

Decision supported: Verified runtime tag and approximately 2.5 GB model artifact; artifact size is not total RAM requirement.

## S19 - Ollama structured outputs

https://docs.ollama.com/capabilities/structured-outputs

Decision supported: Local /api/chat JSON Schema format and Pydantic validation. Shape constraints do not establish factual grounding.

## S20 - Ollama license

https://github.com/ollama/ollama/blob/main/LICENSE

Decision supported: MIT license for the serving software; model weights have separate licenses.

## S21 - Neo4j licensing

https://neo4j.com/licensing/

Decision supported: Community Edition GPLv3 versus separate Enterprise licensing. Do not assume all Neo4j products/features share the same terms.

## S22 - Cytoscape.js reference

https://js.cytoscape.org/

Decision supported: Browser graph visualization API and MIT-licensed library; verify accessibility through a separate table alternative.

## S23 - Docker Compose startup order

https://docs.docker.com/compose/how-tos/startup-order/

Decision supported: depends_on with service_healthy; container start is not equivalent to application readiness.

## S24 - Docker Engine

https://docs.docker.com/engine/

Decision supported: Open-source container-engine baseline; do not equate it with every Docker product.

## S25 - Docker Desktop license

https://docs.docker.com/subscription-billing/desktop-license/

Decision supported: Desktop has separate terms. The Linux Engine plus Compose route does not require Desktop.

## S26 - Compose profiles

https://docs.docker.com/compose/how-tos/profiles/

Decision supported: Optional Ollama/tools profiles; test startup and dependencies for each supported combination.

## S27 - Collector file exporter

https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/fileexporter/README.md

Decision supported: Versioned exporter configuration and JSON-lines capture; verify writable-volume ownership and encoding with a real fixture.

## S28 - Neo4j Docker Compose and secrets

https://neo4j.com/docs/operations-manual/current/docker/docker-compose-standalone/

Decision supported: NEO4J_AUTH_FILE and container secret configuration; verify actual image compatibility.

## S29 - Ollama Docker deployment

https://docs.ollama.com/docker

Decision supported: CPU and optional GPU deployment modes and host-specific prerequisites.

## S30 - PostgreSQL 17 SELECT

https://www.postgresql.org/docs/17/sql-select.html

Decision supported: Row locking and SKIP LOCKED for queue-like consumers; application leases/idempotency are separate responsibilities.

## S31 - Node.js release lifecycle

https://nodejs.org/en/about/previous-releases

Decision supported: Select a supported LTS baseline; Node 24 is the planned baseline, exact patch/digest resolved during implementation.

## S32 - Ollama FAQ and local-only mode

https://docs.ollama.com/faq

Decision supported: Check OLLAMA_NO_CLOUD support in the pinned version; independently verify disconnected operation after downloads.

## Redistribution and verification boundary

No third-party source text, model weights, images, production telemetry or actual benchmark dataset is redistributed in this kit. The included miniature example is newly authored synthetic contract material. Technology selection is researched; application build, inference, training, capture, runtime security and deployment gates remain unexecuted until implementation. This is an engineering specification, not a legal opinion about redistribution.
