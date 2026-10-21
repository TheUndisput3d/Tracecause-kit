# ADR 0006 — immutable graph snapshot publication

Status: accepted for Phase 06 on 2026-10-11.

Keep PostgreSQL authoritative and project only provenance-validated observations to Neo4j under a fresh snapshot ID. Publish the active pointer only after structural, relationship, provenance and index checks. Require readers to pin one READY ID, a complete matching graph marker, scope and cutoff. Do not infer a `CAUSES` edge from `CALLS`. Use leases and tombstoning before scoped graph retirement. The PostgreSQL/Neo4j boundary cannot be atomic, so audit detects partial or missing projections, while reads fail closed. Document and assertion nodes are reserved for the next phase; optional semantic indexes are not enabled.
