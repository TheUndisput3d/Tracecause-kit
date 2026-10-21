# ADR 0001: Foundation ownership and execution model

Status: Accepted for the initial implementation. Date: 2026-10-10.

## Decisions

- **Batch ingestion:** accept bounded OTLP/JSON exports and manifests, then process durable jobs. Reject a streaming broker in v1. This keeps fixture replay, temporal eligibility, and failure recovery inspectable, at the cost of no live stream updates.
- **PostgreSQL authority and Neo4j projection:** relational rows and immutable artifacts own source facts; Neo4j is rebuilt from them. Reject graph-only storage because it would make artifact lineage and publication rollback harder. Graph publication remains a later phase.
- **PostgreSQL-backed jobs:** use transactions and leases in the existing database. Reject Redis/Celery/Kafka until measured throughput requires them. One worker limits contention and simplifies local reproducibility; it also limits parallel throughput.
- **No arbitrary text-to-Cypher:** expose only parameterized, bounded, allowlisted graph queries. Reject LLM-authored executable queries because untrusted text could cross scope or mutate graph state.
- **Split dependency groups:** thin API dependencies install by default; ML and capture dependencies are resolved in the same `uv.lock` but installed only in worker/capture profiles. This avoids loading large model packages for the phase 00 API. The eventual worker image must explicitly sync the ML group.

These decisions preserve the invariants in `AGENTS.md`; they do not claim the later domain paths are implemented.
