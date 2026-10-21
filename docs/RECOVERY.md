# Recovery runbook

Commands run from the repository root with its locked Python environment. Keep backups private. Do not use `docker compose down -v`, delete the generation lock or overwrite a READY graph as a repair.

## Backup

1. Stop accepting imports/investigations, drain or explicitly cancel pending jobs, and stop this deployment's worker. Keep the databases running. Do not stop unrelated services.
2. Configure the existing `TRACECAUSE_PG_*`, `TRACECAUSE_NEO4J_*`, `TRACECAUSE_ARTIFACT_ROOT` and file-secret settings for the source deployment. Use the dedicated PostgreSQL administrator only for migration/backup.
3. Apply additive migration0007. It adds nullable snapshot membership; legacy snapshots are backfilled only after their original graph membership, SQL source hashes and complete projection validate.
4. Run:

```bash
uv run --locked python -m scripts.recovery.backup \
  --postgres-container tracecause-postgres-1 \
  --output /absolute/private/new-backup-directory
```

The command refuses an existing output directory or pending jobs, holds database table SHARE locks during dump/copy, executes real `pg_dump`, verifies every registered artifact and records the schema/counts/dump SHA. Staging and model caches are not backed up. All jobs must be terminal; reprefetch pinned models independently. Preserve failed partial backup directories as diagnostics; a missing manifest is not a complete backup. Resume the source worker after success or a reviewed failure.

## Restore into isolation

Choose a **new** database name beginning `tracecause_restore_`, a nonexistent artifact directory and an empty, separate Neo4j instance. Point settings at that graph, the new artifact root and the local PG administrator. Verify the backup is trusted; PostgreSQL dumps are executable administrative input.

```bash
uv run --locked python -m scripts.recovery.restore \
  --backup /absolute/private/backup-directory \
  --database tracecause_restore_drill01 \
  --postgres-container tracecause-postgres-1 \
  --report /absolute/private/restore-report.json
# Set TRACECAUSE_PG_DATABASE to the restored database before replay:
uv run --locked python -m scripts.recovery.replay \
  --report /absolute/private/replay-report.json
```

Restore refuses an existing database/artifact root and nonempty graph. It checks dump/artifact hashes, restores SQL transactionally, compares schema/table counts and rebuilds the same READY snapshot IDs from the recorded membership. SQL publication timestamps and saved artifacts are preserved. Replay reopens all saved citation texts and runs a separate template investigation. A failed restore remains isolated; inspect it and retry into another new target rather than deleting or overwriting evidence.

Legacy membership cannot be invented if its old graph is already lost or inconsistent. That backup is refused. Backfill before losing the original graph. A changed source/projection algorithm producing a different content hash also blocks reconstruction; restore with the compatible source/image revision.

## Interrupted jobs and snapshots

Jobs retain a 30-second lease and heartbeat every five seconds. After SIGKILL, another worker claims an expired lease, preserves its checkpoint and increments attempts (maximum three). Imports reuse deterministic stage/snapshot IDs. Cancellation and lease ownership are checked immediately before publication. A graph builder crash can leave BUILDING SQL metadata and partial scoped graph nodes; a retry of that ID validates the original membership and publishes only when complete. The previous active snapshot remains usable.

Inventory commands in the existing CLI report stale BUILDING/FAILED nodes and missing artifacts. Never delete an apparent orphan from a Neo4j database shared with another SQL test database: an inventory only knows its own SQL scope. Retirement refuses the active snapshot, live reader leases, pending investigation jobs and saved investigations; it tombstones SQL before scoped graph deletion. Artifact cleanup remains reference-checked and defaults to dry run. Retention/deletion of saved results requires a separate explicit operator policy.

## Interrupted model call

A nonempty `generation.lock` means the provider may still be computing. Stop this deployment's workers. Restart its dedicated Ollama container, then verify the pinned model is ready. The artifact root must contain an operator-owned `provider-project` file with the exact Compose project name. Run:

```bash
uv run --locked python -m scripts.recovery.reconcile_generation \
  --slot-path /absolute/deployment/artifacts/generation.lock \
  --container YOUR_DEPLOYMENT_OLLAMA_CONTAINER
```

The command locks the existing inode, verifies container/service/project identity and a start time after dispatch, writes a durable unique receipt, then clears the fence. It cannot clear a native-provider fence or one associated with an unrelated deployment. Do not substitute `/api/ps`, a timeout, deleting files or a worker restart for provider reconciliation. Keep native generation disabled after an uncertain call until an equivalent operator-reviewed recovery is completed.

## Executed phase16 drill

The local dashboard was quiesced for backup and then restarted. Backup `reports/phase16-backup-v1` contains52 verified artifacts, eight backfilled snapshot memberships and53 terminal jobs. Restore into `tracecause_restore_phase16_v1` plus fresh Neo4j volumes rebuilt eight snapshots. Ten saved investigations /35 citations reopened successfully; one fresh template investigation resolved six citations. `phase16-restore.json` and `phase16-restore-replay.json` record actual results. The initial replay harness omitted two required request fields; it failed explicitly and was corrected before success. No frozen benchmark was rerun.

The optional `compose.recovery*.yaml` files are local diagnostic environments, with explicitly bound loopback ports. The model diagnostic reuses cached weights through a read-only blob mount and its own writable manifest cache. It must not mutate the host provider's model store. Stop diagnostic containers after checks; retained volumes/reports are intentional evidence.
