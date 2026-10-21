# Full application with Docker

This starts the complete local operator application: frontend/Caddy, FastAPI API,
background worker, PostgreSQL, Neo4j knowledge graph, MiniLM embeddings, the bundled
trained ranking model, and Ollama running pinned Qwen. No host Python, Node, uv,
Neo4j, PostgreSQL or Ollama installation is required.

## Start

From the repository root, with Docker Engine and Docker Compose installed
(verified on Linux/amd64 with CPU inference):

```sh
docker compose -f compose.full.yaml up --build -d --wait --wait-timeout 2400
```

Open **http://127.0.0.1:14000**. `make docker-up` is an equivalent shortcut.
The first startup downloads model files (including approximately 2.5 GB for Qwen),
builds the application and initializes fresh databases. Follow progress with:

```sh
docker compose -f compose.full.yaml logs -f models migrate seed
```

Persistent volumes keep model downloads, credentials, traces, documents and results
across restarts. Later starts reuse the caches. Startup fails if model download,
digest verification, schema probing, migration or seed validation fails; a running
Ollama process alone is not treated as ready AI.

## Run an investigation

1. Get your locally generated operator token:
   ```sh
   docker compose -f compose.full.yaml exec -T api cat /run/secrets/operator_token
   ```
2. Click **Connect operator** in the dashboard and paste the token.
3. Open a seeded episode in **Incident library**, select a service and a cutoff.
   Parcel Shop, Newsroom and Media Works each provide two actual captured incidents.
   Their public index supplies a useful initial cursor and symptom service.
4. Expand **Investigation settings** and choose **ollama** under Generation to run Qwen, or **template** for a deterministic
   explanation. Select GRAPH, lexical, dense or hybrid retrieval as needed.
5. Run the investigation and inspect the cited measurements and dependency paths.

The bundled v4 ranker is available as an explicit experimental option. Heuristic
ranking remains the default: captured development improved but synthetic accuracy
regressed, with no independent production validation. Qwen selects typed fact IDs;
the application renders exact values/citations and validates them against source
evidence. The UI identifies this authorship boundary. Provider/plan failures can
still visibly fall back to a template. Saved reports are playback; opening one
never invokes the model. See [MODEL_CARD](MODEL_CARD.md) and
[reliability verification](phase-reports/reliability.md).

## What starts automatically

| Service | Purpose |
|---|---|
| bootstrap | Generates persistent random credentials and initializes volume ownership |
| postgres / neo4j | Authoritative records and the graph projection |
| ollama | Local pinned Qwen server, with one concurrent generation |
| migrate | Applies SQL and graph schema migrations using isolated admin credentials |
| models | Downloads/caches MiniLM, Qwen tokenizer and Qwen weights; verifies digest and actual schema generation |
| seed | Registers the bundled ranker, imports the synthetic demo/runbooks, saved evaluation reports and six public operational captures, and validates source-backed snapshots/citations |
| api / worker | HTTP API, durable imports and investigation jobs |
| web | Production frontend with same-origin API proxy |

Bootstrap, migrate, models and seed are one-shot jobs: `Exited (0)` is their normal
successful state. The other six services remain running and healthy. The demo seed
is idempotent for unchanged bundled sources; restarting does not create another
snapshot or erase existing work.

The operational seed imports chronologically so each second incident can pin an
earlier snapshot. Versioned replay/document-scope upgrades publish a new immutable
snapshot while preserving earlier snapshots and saved reports. An unrelated
corpus update does not replace an already valid original demo snapshot.

## Operations

```sh
# Status and application logs
docker compose -f compose.full.yaml ps --all
docker compose -f compose.full.yaml logs -f --tail 100 api worker

# Stop while retaining all databases, models and credentials
docker compose -f compose.full.yaml stop

# Start again, rebuilding changed application code
docker compose -f compose.full.yaml up --build -d --wait --wait-timeout 2400
```

Only the frontend port is published, on loopback. Database and Ollama ports remain
inside Docker. This operator stack enables uploads and computation with the token;
the separate `compose.release.yaml` workflow serves a read-only release.
Existing development/release projects use separate names, ports and volumes.

Override the frontend port or the Ollama CPU cap when needed:

```sh
FULL_WEB_PORT=14001 FULL_OLLAMA_CPUS=4 docker compose -f compose.full.yaml up --build -d
```

The default Ollama cap is 8 CPUs and 6 GiB RAM; the whole stack requires additional
memory for the worker, graph, API and database. Plan about 12–16 GiB available RAM
and 25–40 GB disk for images/caches/data. These are planning allowances, not measured
usage guarantees. Lower CPU limits can approach the bounded generation timeout.
GPU support is not required for this CPU configuration.

Credentials are created in Docker volumes. API and worker never receive PostgreSQL
admin credentials. Do not delete the secret volumes while retaining databases:
regenerated passwords would not match existing database roles. No service mounts
the Docker socket, private evaluation labels or the host's model cache.

Training, held-out evaluation and fault-injection capture are explicit offline
workflows described in EVALUATION_PROTOCOL and CAPTURE. Startup loads the existing
trained artifact and saved evaluation reports; it does not retrain or rerun frozen
experiments. The five-service MiniShop capture lab remains a separate capture stack.

## Verified locally

The full stack passed actual queued Qwen generation, browser operator workflows,
multipart upload, citation resolution and stop/up persistence on 2026-10-12.
The initial deployment preserved two graph snapshots and five saved results across
restart. The subsequent reliability update preserved five snapshots and12 saved
responses plus credentials, with six healthy runtime services and four successful
startup jobs. See the [initial report](phase-reports/docker-full-stack.md) and
[reliability report](phase-reports/reliability.md) for commands, retained evidence
and limits.
