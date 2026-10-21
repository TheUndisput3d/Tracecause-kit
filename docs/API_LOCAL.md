# Local API and durable worker

For the complete application, use the [Docker startup](DOCKER_FULL_STACK.md).
It serves this API at `http://127.0.0.1:14000/api/v1` and creates credentials,
databases, models and the worker automatically. The manual profile below is for
backend development.

## Operational graph API

All three reads bind `episode_id`, `snapshot_id`, `as_of` and `temporal_mode`.
They validate source artifacts and the scoped graph projection before returning.

| Endpoint | Result |
|---|---|
| `GET /api/v1/operations` | Typed operational entities/relationships, `CALLS` and `MESSAGE_FLOW` paths, measured change explanations and pinned prior-incident comparisons |
| `GET /api/v1/operations/evidence/operation:{uuid}` | Original typed receipt rendered as canonical JSON, source bundle hash and inspection scope |
| `GET /api/v1/operations/handoffs/handoff:{uuid}` | Exact normalized producer/consumer records, hashes, byte offsets and derivation rule |

The first endpoint optionally accepts `symptom_service_id`. Use the episode's
public `default_symptom_service_id` and `default_as_of` for its curated starting
view; both are optional metadata, not expected diagnostic answers. Follow each
historical citation's own episode, snapshot, cutoff and temporal mode.

`event_time_replay` uses event occurrence. `available_time_replay` additionally
requires reliable per-record arrival timestamps and measurements materialized in
that mode. The public operational corpus is event-time only; unsupported replay
returns an explicit 422 error. Unknown scope returns 404, and inconsistent source
or projection data fails closed. Read bounds and truncation are part of the
[operational architecture](architecture/operational-graph.md).

The episode `series` endpoint returns one source-verified observation per
service and ten-second interval. When immutable replay materialization retains
several versions, it selects the eligible version with the greatest recorded
sample coverage and keeps that observation's ID and statistics intact. Counts
are never summed across repeated versions. The point limit applies after this
selection, and the response states whether it was truncated.

Run from the repository root with the pinned `uv` installation on PATH. This profile binds to loopback. PostgreSQL and Neo4j must already be healthy (`make up`); apply migrations with `make migrate`. The default API is read-only. Owner mode requires an explicit secret file and a separate worker.

## Owner setup

Create a new local token once (exclusive creation avoids replacing an existing token):

```bash
python3 - <<'PY'
import os
import secrets
fd = os.open('.secrets/operator_token', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(secrets.token_urlsafe(32) + '\n')
PY
```

In each of the API and worker terminals:

```bash
export TRACECAUSE_PUBLIC_READ_ONLY=false
export TRACECAUSE_OPERATOR_TOKEN_FILE="$PWD/.secrets/operator_token"
export TRACECAUSE_PG_HOST=127.0.0.1 TRACECAUSE_PG_PORT=15432
export TRACECAUSE_PG_DATABASE=tracecause TRACECAUSE_PG_USER=tracecause_app
export TRACECAUSE_PG_PASSWORD_FILE="$PWD/.secrets/postgres_password"
export TRACECAUSE_NEO4J_URI=bolt://127.0.0.1:17687
export TRACECAUSE_NEO4J_AUTH_FILE="$PWD/.secrets/neo4j_auth"
export TRACECAUSE_ARTIFACT_ROOT="$PWD/reports/api-local/artifacts"
export TRACECAUSE_STAGING_ROOT="$PWD/reports/api-local/staging"
```

API terminal:

```bash
uv run --locked uvicorn tracecause.api.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Worker terminal:

```bash
uv run --locked --group ml python -m tracecause.jobs.api_worker
```

One API process and one API worker are the supported initial profile. The older `tracecause worker run` handles CLI span imports only. The API worker performs span import, authored documents, signals and graph publication. Stop a foreground process with Ctrl-C. A worker interrupted during a job leaves a recoverable lease; another worker can reclaim it after 30 seconds. Three failed attempts end the job. Cancellation is checked between stages and immediately before publication.

## Exercise the API

This Python client reads the token without putting it into command-line arguments. Use a generated public episode bundle containing its declared source files:

```bash
uv run --locked python - <<'PY'
import json
import time
from pathlib import Path
import httpx
root = Path('data/generated/seed-20261010/03c9ef66-2609-5aec-86fa-9dda41e1c9af')
manifest = json.loads((root / 'manifest.json').read_text())
headers = {'Authorization': 'Bearer ' + Path('.secrets/operator_token').read_text().strip()}
with httpx.Client(base_url='http://127.0.0.1:8000', timeout=30) as client:
    print(client.get('/health/ready').json())
    response = client.post('/api/v1/imports', headers={**headers, 'Idempotency-Key': 'local-import-v1'},
        data={'manifest': json.dumps(manifest)},
        files=[('files', (s['logical_name'], (root / s['logical_name']).read_bytes()))
               for s in manifest['sources']])
    response.raise_for_status()
    def wait(job_id):
        for _ in range(180):
            response = client.get('/api/v1/jobs/' + job_id)
            response.raise_for_status()
            job = response.json()
            if job['status'] in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
                if job['status'] != 'SUCCEEDED':
                    raise RuntimeError(job)
                return job
            time.sleep(1)
        raise TimeoutError('Job is still pending; inspect its status before resubmitting.')
    wait(response.json()['job_id'])
    episode_id = manifest['episode_id']
    episode = client.get('/api/v1/episodes/' + episode_id).json()
    request = dict(episode_id=episode_id, snapshot_id=episode['active_snapshot_id'],
        as_of=manifest['event_interval']['end'], temporal_mode='event_time_replay',
        question='Which service should be checked?', symptom_service_id=None,
        retrieval_mode='LEXICAL', ranking_mode='heuristic', generation_mode='template')
    response = client.post('/api/v1/investigations', json=request,
        headers={**headers, 'Idempotency-Key': 'local-investigation-v1'})
    response.raise_for_status()
    submitted = response.json()
    wait(submitted['job_id'])
    view = client.get('/api/v1/investigations/' + submitted['investigation_id']).json()
    print(json.dumps(view, indent=2))
    for evidence in view['result']['evidence']:
        opened = client.get('/api/v1/evidence/' + evidence['evidence_id'], params={
            'snapshot_id': view['snapshot_id'], 'as_of': request['as_of'],
            'temporal_mode': request['temporal_mode']})
        opened.raise_for_status()
PY
```

The 202 `investigation_id` identifies the durable operation. Its view contains an immutable `result` whose own ID is the domain cache identity. Both IDs resolve through the investigation endpoint; saved-result lists contain domain IDs. Export returns the same view with a download header. Reading a saved result never runs inference.

Idempotency is bound to workspace, operation kind and canonical input. Reusing a key with different input returns 409. A replay preserves the original frozen snapshot. Use a new key for a genuinely new request. Keys are 1–200 printable ASCII characters without spaces. Ten queued/running/retry jobs fill the admission queue (429).

Uploads accept only a JSON manifest form field and up to 100 declared `.json`, `.jsonl` or `.md` files: 25 MiB per file, 100 MiB for the entire multipart request including framing. Files must match declared byte counts and SHA-256. Names are flat, with no archives, URLs or paths. Markdown uses the authored frontmatter contract described in `DOCUMENTS.md`; its source ID matches the filename stem. A baseline interval is required. Staging directories are server-generated and private. Failed upload validation removes the new staging directory; crash leftovers are retained for reconciliation.

## Replay and health

Set `TRACECAUSE_PUBLIC_READ_ONLY=true` for replay; all mutation routes return 403 even with a token. Keep the same storage configuration. `/api/v1/config` describes safe capabilities. `/health/live` checks process responsiveness; `/health/ready` checks SQL access, active SQL snapshot states, graph connectivity and the most recently created episode's active graph marker. Empty storage is healthy. Missing optional local model weights sets `generation_degraded`; no inference is run by readiness. Local generation requires the separate pinned model opt-in described in `LOCAL_GENERATION.md`.

Metadata pagination is ordered by `(created_at,id)`, at most 100 items. Cursors bind collection, workspace and filters. Graph/evidence/series requests bind episode/snapshot/time on the server; event nanoseconds are strings. Unknown fields, invalid cutoffs, unsupported enum values and foreign resources fail with safe error envelopes and request IDs. Internal import stages are not exposed.

Owner-only local report registration (no external hosting):

```bash
uv run --locked python -m tracecause.api.publish_report --report PATH_TO_SANITIZED_EVALUATION_REPORT.json
```

Only sanitized numeric canonical aggregates pass registration, and published reports are immutable. The public evaluation endpoints never read private labels. Historical job timestamps unavailable before migration 0006 remain null; they are not inferred from heartbeats. OpenAPI is at `/openapi.json`; `make contract` updates checked-in schemas and frontend types.

## Limits of this checkpoint

Single default workspace and single-user operation; no multi-tenant claim. Optional generation failures fall back with an explicit origin and reason. Captured evaluation found unsupported/partially supported hypotheses in the frozen template version. Those artifacts remain unchanged; application grounding repair and its new validation are release requirements. Containerized API/web operations, performance measurements and recovery release checks belong to subsequent phases.

## Citation offset semantics (runtime v7)

`Evidence.locator.record_offset` is a byte offset into the normalized JSONL file
identified by `source_hash`; `span_ids[0]` identifies that record. Normalized rows
retain their original OTLP `source_hash` and `source_record_offset` as lineage.
Internal graph support `source_record_offset` remains an original OTLP offset.
Pre-v7 saved results retain historical mismatched offsets and are marked in the
UI. `/evidence/{id}` resolves current positions within the requested snapshot and
cutoff. Opening evidence does not overwrite an immutable saved report.

## Connected knowledge and authored checks (v8 follow-up)

`GET /api/v1/knowledge` and `GET /api/v1/knowledge/checks` use the same explicit
`episode_id`, `snapshot_id`, `as_of` and `temporal_mode` as `/graph`. The latter also
requires the canonical 64-character `symptom_service_id`. They are read-only and
never trigger an LLM call. Exact typed contracts appear in OpenAPI. Source mismatch
returns the existing sanitized 409 `resource_conflict`; a missing service returns
404. Limits, semantics and source inspection are documented in KNOWLEDGE_GRAPH.md.
