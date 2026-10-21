# PostgreSQL imports, artifacts and worker operations

Phase 04 uses PostgreSQL as the authoritative store. Alembic revisions `0001` and `0002` create the tables and access indexes in `docs/BACKEND.md` B3. The repository supports one configured workspace. An episode can be `QUEUED`, `IMPORTED`, `FAILED` or `CANCELLED`; `IMPORTED` means redacted spans committed, **not** that a graph snapshot is ready. The active snapshot pointer stays null until a later validated graph publication. No Neo4j write is part of this import transaction.

For a local developer database, start PostgreSQL and Neo4j with `make up`, then run `make migrate`. The migration command reads `.secrets/postgres_admin_password` and never prints the password. `tracecause db health --password-file .secrets/postgres_password` checks the runtime app role. `make test-integration` creates a uniquely named, empty real PostgreSQL database, applies migrations, runs the job/artifact tests and removes only that test-owned database after the test engines close. These tests require the local database services and secret files; no fake backend substitutes for them.

The content-addressed repository stores only redacted normalized JSONL for trace imports. Writes stream into a mode-restricted temporary file, hash and fsync it, then create the final SHA-256 path without overwriting an existing artifact. Reads verify the full hash. The database stores the redacted artifact hash, original source hash, redaction/normalization version, scoped episode reference and span keys. A persisted span resolves to the normalized artifact by `(trace_id, span_id)`; its `source_record_offset` and resource/scope/span indexes describe the original input location, whose raw bytes are not retained by this path. Do not present those original offsets as a retrievable raw citation. Future evidence citations should resolve to the redacted normalized span artifact.

Use `tracecause import submit --episode-dir <public-episode-dir> --artifact-root <absolute-local-artifact-root> --idempotency-key <operator-key> --password-file .secrets/postgres_password` for a host import. The command validates/normalizes before queuing, writes a redacted artifact, then atomically registers the episode, reference and job in PostgreSQL. The key is hashed before storage. The same scoped key/payload returns the existing job; the same key with changed input conflicts. A second key for the same episode/source/version also returns the existing job. A failed or cancelled job is not silently replaced by resubmission.

Run `tracecause worker run --artifact-root <same-root> --password-file .secrets/postgres_password --once` to process one pending job, or omit `--once` for a single polling worker. The worker claims with `FOR UPDATE SKIP LOCKED`, a 30-second lease, heartbeat and at most three attempts. It verifies the artifact, writes a durable checkpoint, then inserts spans in bounded batches within one transaction. Existing matching span keys are reused; a conflicting semantic payload rolls back the transaction and fails the job. The job reaches `SUCCEEDED` and the episode reaches `IMPORTED` in the same commit. A crash after the checkpoint leaves an expiring lease; the next worker can reclaim the same job. `tracecause job show --job-id <uuid> --password-file ...` exposes only safe status, attempt, checkpoint and error codes. `tracecause job cancel` stops a queued job immediately or requests cancellation at the next worker checkpoint. After repairing a failed import's artifact or dependency, `tracecause job retry --job-id <uuid> --password-file ...` explicitly requeues that failed job with the same inputs.

For the Compose worker, run `make build` and `make migrate`, then stage the smoke fixture into the shared artifact volume with:

```bash
docker compose run --rm --no-deps \
  -v "$PWD/data/fixtures/smoke:/fixtures:ro" \
  --entrypoint tracecause worker import submit \
  --db-host postgres --db-port 5432 --db-name tracecause \
  --db-user tracecause_app --password-file /run/secrets/postgres_password \
  --episode-dir /fixtures/0eeb41fb-4596-588e-8a2d-e6ba464fbf29 \
  --artifact-root /var/lib/tracecause/artifacts \
  --idempotency-key compose-smoke-v1
make worker-up
```

`make worker-down` stops only the worker. The worker uses named artifact and staging volumes, runs as the non-root app user, and reads the app password from a Compose secret. Keep the artifact volume across worker restarts. The startup command does not run migrations as the app user.

`tracecause db reconcile --artifact-root <same-root> --password-file ...` reports missing, unreferenced and unregistered artifact files, expired jobs, abandoned BUILDING snapshots and aged temporary files. `tracecause db cleanup --artifact-root <same-root> --password-file ...` is dry-run by default; `--apply` is required to remove eligible temporary files or a specified unreferenced artifact. A referenced artifact cannot be removed. `tracecause db backup-metadata --artifact-root <same-root> --password-file ...` stores only schema revision and row counts as a hashed artifact. It intentionally stays outside the application database and appears as an unregistered artifact in reconciliation; retain it separately. It is **not** a database or volume backup. Full backup/restore rehearsal belongs to Phase 18.

The artifact filesystem and PostgreSQL do not share a transaction. A failed DB registration can leave an unreferenced artifact; reconciliation reports it. The worker's database import is atomic, but future Neo4j graph publication needs a separate validated snapshot protocol. Keep evaluator `data/gold/` outside both runtime images and artifact roots.
