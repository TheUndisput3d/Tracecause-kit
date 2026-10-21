# Local release operations

Public hosting is **not deployed**. This runbook binds only loopback. The local
container verification is tracked in `phase-reports/18.md`; never interpret a
configuration example or unfinished gate as a running deployment.

## Build and bootstrap

Install locked development tooling first (`CI.md`). From the repository root:

```sh
make release-prepare
make release-build RELEASE_VERSION=local-v1
make release-up
```

The build command refuses existing tags and archives the exact source files before
building. Choose a new version for another build. It records local content-addressed
image IDs and writes those IDs into `.env.release`; no image is pushed to a registry.
Failed builds retain their source/logs and require a new version on retry.

`.env.release` selects pinned base images, application image identities and the
loopback port13000. `release-up` starts fresh databases if absent, initializes only
named-volume ownership, migrates, explicitly prefetches pinned MiniLM and Qwen
**tokenizer** assets, and seeds one synthetic public episode plus two measured
aggregate reports. These setup steps require network access for uncached images/
packages/model assets. Normal API/worker operation is offline with respect to
model assets. There is no paid provider configuration. The initial report is an
actual computed template result and is marked replayed when served.

The API runs as UID/GID10001, with read-only image and artifact volume; staging is
writable. Database credentials are file secrets. Runtime API/worker never receive
the PostgreSQL administrator secret. `.secrets/release` is host0700; explicitly
mounted secret files are0444 because local Compose bind secrets retain host modes.
Never copy these files into images, Git, browser assets or logs.

The default is read-only replay and no worker/model server. For local operator
work, set `PUBLIC_READ_ONLY=false` in the ignored `.env.release` and run:

```sh
docker compose --env-file .env.release -f compose.release.yaml --profile operator up -d --wait api worker web
```

Connect using the local operator token through the dashboard. Restore
`PUBLIC_READ_ONLY=true`, stop the owned worker, and recreate API/web before treating
this as a read-only demo. Never publish the operator variant to the internet.
No database or model port is published by this release configuration.

## Health, limits and logs

`/health/live` checks the API process; `/health/ready` checks SQL/schema and graph
readiness. Worker health checks a loop heartbeat (maximum600seconds old) and actual
SQL/Neo4j connectivity. A heartbeat is not proof a long job made progress; inspect
job status/lease and stage logs. The worker finishes its current bounded job on
SIGTERM; Compose allows210seconds before SIGKILL. Existing durable leases and
model admission fences handle a forced interruption.

One worker/one model slot are the supported baseline. Graph, upload, queue and
question bounds remain server-side. Numerical libraries are limited to one thread.
Compose CPU/memory limits are guardrails, not measured capacity promises. JSON
container logs rotate at10MiB, three files. Use `docker compose --env-file
.env.release -f compose.release.yaml logs --tail 100 api worker` and `docker stats`.
Private authenticated metrics are not proxied by Caddy and are forbidden in public
mode. Do not expose Docker, Neo4j browser, API docs or model endpoints.

Failed jobs retain a safe error code. Reclaim only expired leases through the
worker. Failed graph builds never replace the active READY snapshot. Investigate
source hashes/membership before retry; do not edit an immutable snapshot. Uncertain
model dispatches keep admission closed; see `RECOVERY.md` for verified dedicated
provider restart reconciliation. Never delete a generation lock to resume traffic.

## Local TLS rehearsal

The TLS overlay binds127.0.0.1:18443 and uses a dedicated Caddy local CA in its own
volume. It does not install a CA in the host/browser trust store. Run with both
`compose.release.yaml` and `compose.release.tls-local.yaml`; retrieve its root
certificate and pass it explicitly to `scripts.deployment.smoke --ca PATH`.
The browser drill suppresses its own trust prompt only after the HTTP smoke has
validated the certificate and hostname with that CA. External DNS, publicly
trusted TLS, firewall verification and public publication require separate owner
authorization and remain NOT DEPLOYED.

Configuration uses Caddy's documented [environment expansion](https://caddyserver.com/docs/caddyfile/concepts#environment-variables)
and [local TLS issuer](https://caddyserver.com/docs/caddyfile/directives/tls).

## Stop and persistence

`make release-stop` stops only the release Compose project and retains every named
volume. Never use `down -v` in ordinary shutdown or upgrade commands. Restart with
the same image IDs and secrets; database roles/passwords do not automatically
change when files are edited. Credential rotation needs a coordinated database
password change and application restart, with the previous backup retained.

## Backup and rollback

Use `RECOVERY.md` for the implemented consistent dump/artifact manifest, membership
backfill and new-database/empty-graph restore checks. Pause all writers and drain
or cancel pending jobs first; staging is intentionally excluded. Retain7daily and
4weekly backups as a proposed policy, subject to available disk. Run through a
user systemd timer only after selecting a backup directory and checking the
writer-pause procedure; no scheduler is silently installed.

Before rollout, record immutable image IDs, schema revision, active snapshot IDs,
source manifest and verified backup. Upgrade only to an explicitly selected image
and migration whose compatibility was reviewed. For an additive compatible schema,
select the recorded previous image IDs and retained snapshot, recreate application
containers, then rerun readiness and every saved citation. A destructive migration
requires restoring the matching backup into new volumes/database; do not execute
a blind schema downgrade or overwrite current storage. Preserve newer writes for
operator reconciliation before switching traffic.

## Data deletion

Stop writers, take a reviewed backup, and inspect snapshot/artifact retention
references. Active snapshots, reader leases, pending jobs and saved investigations
prevent retirement. Remove only a specifically selected dataset after explicit
owner authorization; this runbook deliberately contains no blanket volume deletion.
Model caches can be prefetched again, but deletion is a separate requested action.

## Backup commands

After returning to the default HTTP read-only release and stopping the worker:

```sh
make backup BACKUP_DIR=reports/backups/CHOOSE_NEW_NAME
```

The wrapper checks public mode, refuses a running worker, exports artifact bytes
through Docker, verifies SQL/source membership using private bridge addresses, and
creates a dump/hash manifest. It uses the host Docker administrator connection;
no Docker socket is mounted into application containers. A concurrent manually
started seed/migration tool is also a writer and must finish first.

`make restore-check` requires an explicit new `RESTORE_DB`, `BACKUP_DIR`,
`POSTGRES_CONTAINER`, `RESTORE_REPORT` and isolated `TRACECAUSE_*` settings. It
refuses an existing database/artifact directory or nonempty target graph. Then run
`scripts.recovery.replay` against that restored state to reopen saved citations
and compute a fresh template result. Use a new restore Compose project/volume each
time; do not reuse an old graph merely because its container is stopped.

The optional provider uses `--profile llm` and the pinned image in `.env.release`.
Weights are never pulled by requests. Either explicitly invoke `tracecause models
pull` from the operator tools container, or use `scripts.deployment.import_model_cache`
with a local cache: it verifies the exact manifest and every selected blob, copies
only those files into an empty owned release volume, and leaves the source untouched.
The public API stays read-only with provider generation disabled even when the
separate model container is running for an operator drill.

## Verified image selection

With a current backup and the worker stopped, select a recorded local manifest:

```sh
uv run --locked --group ml python -m scripts.deployment.rollout \
  --manifest reports/releases/CHOSEN_VERSION/manifest.json \
  --output reports/rollouts/CHOOSE_NEW_NAME
```

The tool checks the actual database migration revision against the archived
image source, requires read-only mode, verifies image IDs/labels, and compares
saved result bytes and snapshot identity before/after recreation. Only after
successful verification does it persist the selected IDs in `.env.release`.
Automatic rollback is restricted to the **same schema revision**; a changed
migration needs a separate compatibility review/restore. A failed rollout retains
its logs and requires explicit selection of the previous recorded manifest.

Phase18 exercised v5→v4→v5 at schema0007, with identical saved result/snapshot.
PostgreSQL, Neo4j, API and web were then restarted and the same identities survived.
The read-only v5 release remains available locally on http://127.0.0.1:13000.
The model and worker are stopped. Native Ollama and the earlier development stack
are separate owned environments. No public host or GitHub publication is claimed.
