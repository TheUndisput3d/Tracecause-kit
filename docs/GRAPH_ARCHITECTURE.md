# Temporal graph projection

PostgreSQL is authoritative for episodes, redacted spans, observations, support references, document/chunk metadata and the active snapshot pointer. Neo4j stores a rebuildable snapshot-local projection. A graph snapshot contains `Snapshot`, `Service`, `Observation` and `Incident` nodes; Phase 07 adds `Document`, `Chunk` and `Assertion` nodes from authored source artifacts. Schema 1.2 also labels `Observation` and `Chunk` nodes `Retrievable` for a common scoped evidence index; their source-specific labels and provenance remain authoritative. `CALLS` runs from caller to callee and carries exact PostgreSQL observation IDs. It is a measured dependency, never a `CAUSES` fact.

`tracecause graph migrate --neo4j-auth-file .secrets/neo4j_auth` installs versioned constraints and indexes. `make migrate` runs both PostgreSQL and Neo4j migrations. Import and materialize signals first, then run:

```bash
tracecause graph build --episode-id <uuid> --password-file .secrets/postgres_password --neo4j-auth-file .secrets/neo4j_auth
tracecause graph audit --password-file .secrets/postgres_password --neo4j-auth-file .secrets/neo4j_auth
tracecause graph subgraph --episode-id <uuid> --snapshot-id <uuid> --as-of-ns <epoch-ns> --password-file .secrets/postgres_password --neo4j-auth-file .secrets/neo4j_auth
```

`make snapshot-build EPISODE_ID=<uuid>` and `make graph-audit` are equivalent operator shortcuts for the build and audit commands.

The build registers `BUILDING` in PostgreSQL, writes under a new snapshot ID, checks required indexes, exact node and relationship sets, scope and support, marks the Neo4j marker complete, then atomically marks `READY` and updates the active pointer in one PostgreSQL transaction. The two databases cannot commit atomically. A process failure can leave an incomplete graph or a complete marker without a READY row. Readers pin an explicit READY snapshot ID for the whole request and check its matching complete marker. Thus incomplete builds are invisible and a rebuild leaves the previous READY snapshot readable. `graph audit` reports stale BUILDING rows, invalid or missing READY markers, orphan graph markers and partial FAILED snapshots for operator reconciliation.

Every read requires workspace, episode, snapshot and nanosecond cutoff. A `CALLS` traversal is accepted only if one support observation and its exact source spans qualify by event time, source availability when requested, caller/callee identity and the six-minute topology lookback. Source-available replay refuses missing reliable availability timestamps; the synthetic fixtures use event-time replay. Neo4j query templates use bound values and three-second query timeouts. Reads cap graph edges at 120, nodes at 80, paths at three and hops at three; graph support IDs are capped at 20,000. The public CLI exposes `neighbors`, `paths`, `observation` and `subgraph` with these bounds.

`tracecause graph retire ...` is dry-run by default and reports active-pointer status, live reader leases, scoped graph node count and episode artifact references. `--apply` refuses an active snapshot or live reader lease, tombstones the PostgreSQL row, then deletes only graph nodes carrying that snapshot ID. Artifacts remain referenced to the episode and are not deleted by this operation. A crash between tombstone and graph deletion leaves a retryable tombstoned projection, not a readable partial graph. The 30-second read leases are suitable for bounded local reads; a later service rollout needs renewal for longer requests and a job lease policy.

The graph describes service communication and symptoms. Causal hypotheses belong to investigation results and need independent evidence. Sampling, missing spans, alias mistakes and topology changes can all make observed dependencies incomplete.
