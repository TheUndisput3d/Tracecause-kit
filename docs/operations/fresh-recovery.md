# Isolated fresh-stack and recovery drill

This drill verifies the final local Docker images against empty application
storage, then restores a verified backup into a new database, artifact directory
and empty graph. It also reopens the operational graph's source receipts, measured
signals, historical references and asynchronous handoff evidence.

## Prerequisites

Build the final backend and web images using `compose.full.yaml`. The existing
`tracecause-full` project must have initialized the pinned Hugging Face and Ollama
model caches. Stop concurrent local Qwen evaluation jobs while the fresh stack's
model bootstrap runs. The drill has its own Ollama service and performs the usual
model digest and schema probe.

The script copies only model-cache files from read-only mounts of the existing
cache volumes. The new project receives separate application artifacts, staging,
PostgreSQL, Neo4j, generated credentials and model-cache destinations. This is a
fresh application-state verification with preobtained model files; it does not
measure a first network download.

The host Python environment needs the project's locked dependencies and Docker
access. Use a unique project name beginning with `tracecause-proof-` and a new
report directory. The script refuses existing project containers or volumes.

## Run

From the repository root, after final image verification:

```bash
.venv/bin/python -m scripts.deployment.full_recovery \
  --project tracecause-proof-20261019-v1 \
  --output reports/operational-capstone/fresh-restore-20261019-v1
```

Defaults are loopback ports `14001` for the fresh UI, `25434` for its diagnostic
PostgreSQL connection, `27689` for the source graph and `27690` for the empty
restore graph. These extra database ports belong to the isolated drill override;
its databases join a dedicated diagnostic bridge so Docker can bind those
loopback ports. The normal full-stack configuration exposes only the web service. CLI flags can
select other unoccupied ports.

## What is checked

1. Bootstrap and model initialization finish successfully. All six runtime
   services become healthy. The API exposes the demo and six captured operational
   episodes, with local generation readiness.
2. The drill stops its own writers and local model service, copies source
   artifacts, and records exact active snapshot IDs, immutable memberships and
   saved-result artifact hashes.
3. Each operational view uses its public demonstration cursor and entry service.
   The audit resolves all displayed operational receipts, dependency proof
   samples, aggregate/baseline citations and historical state/measurement
   references. Both asynchronous episodes and all three historical comparisons
   must be present.
4. The existing backup utility locks authoritative tables, verifies all registered
   artifacts, records schema/table counts and creates a hashed PostgreSQL dump.
5. The restore utility creates a new database, copies verified artifacts to a new
   directory and rebuilds all READY snapshots in an empty dedicated Neo4j graph.
6. The operational audit runs again. Snapshot IDs, memberships, saved-result
   hashes, operational response hashes and resolved-proof hashes must match.
   Saved investigation citations are reopened and a fresh template investigation
   is executed against restored sources.

On success, `report.json` records image IDs, copied cache provenance, restore
counts and proof coverage. Separate logs and `before.json`/`after.json` retain the
evidence. A failure exits nonzero and retains its stage logs for diagnosis.

If the host-side recovery harness fails immediately after a verified startup,
`--resume-after-startup` can continue from that quiesced state. It requires the
seven-episode readiness record, copied source artifacts, exactly the owned startup
containers and stopped writers/model service. It refuses an already-started
backup or restore transaction. Original failed logs are retained, and the final
report discloses the resumed execution.

The report directory also contains a private credential directory and database
backup. It belongs under the ignored local `reports/` tree; it is not a public
portfolio artifact. Publish only reviewed summaries and selected redacted logs.

The script stops its own containers after a successful run and preserves its
volumes. It does not stop or alter the original project. To stop a failed drill,
use its exact project name and generated override:

```bash
docker compose -p tracecause-proof-20261019-v1 \
  -f compose.full.yaml \
  -f reports/operational-capstone/fresh-restore-20261019-v1/compose.drill.yaml \
  stop
```

Actual release results are recorded separately in the
[operational capstone report](../phase-reports/operational-capstone.md).
