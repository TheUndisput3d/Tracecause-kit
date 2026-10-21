# Docker, environment and operations specification

## D1. Deployment units and design boundary

The application uses five always-on services: `web`, `api`, `worker`, `postgres`, and `neo4j`. An optional `llm` profile adds `ollama`. One-shot `migrate` and `models` services are operator tools, not long-running daemons. The MiniShop capture application is a separate Compose project, not another dependency of `make demo`. No Kubernetes, paid cloud or external managed database is required.

`blueprints/compose.yaml`, `Dockerfile.backend`, `Dockerfile.web`, `Caddyfile`, `.env.example`, `.dockerignore`, `compose.gpu.yaml` and `otel-collector.yaml` are implementation blueprints. They are not an already-running application: phases 00/04/10/12/18 must implement their referenced modules/commands, resolve image references and verify the final configuration. Never describe a parsed YAML file as a successful deployment.

## D2. Services, ports and starting resource caps

`web`: production Vite static build served by Caddy, with `/api/*` and `/health/*` proxied to `api:8000`. Listen on container port 8080, publish only `127.0.0.1:3000:8080` locally. Initial cap 128 MiB and 0.5 CPU. The development Vite server runs separately on localhost:5173 with an explicit API proxy; it is never the production web server.

`api`: the backend image running `uvicorn tracecause.api.main:app --host 0.0.0.0 --port 8000 --workers 1`. Internal port 8000 only; initial cap 768 MiB and 1 CPU. Only its explicitly configured ingest staging directory is writable; artifacts it serves are read-only.

`worker`: same image, command `python -m tracecause.jobs.worker`. No host port. Initial cap 2 GiB and 2 CPU. Owns artifact writes, embeddings, imports, graph publication and investigations. Set numerical thread limits to avoid oversubscribing the laptop. One worker and one generation slot simplify reproducible behavior.

`postgres`: verified pinned PostgreSQL 17 image; internal 5432; 768 MiB/1 CPU starting cap; named persistent data volume. Use an initialization/migration admin secret and a separate non-superuser `tracecause_app` runtime role. Read passwords from mounted secrets; the API/worker never mount the admin secret. `pg_isready` is a startup health check, not an application migration check.

`neo4j`: verified pinned Community image; internal Bolt 7687 and HTTP 7474; 2 GiB/1.5 CPU starting cap. Initial heap and max heap 768 MiB; page cache 512 MiB, leaving headroom for native memory. Store data and logs in named volumes. No Enterprise flag or GDS/APOC plugin is required. Read Neo4j auth from a mounted secret; its supported Docker `_FILE` mechanism is documented [S28]. A Cypher health check must actually authenticate and return 1.

`ollama`: optional pinned image; internal 11434 only; 6 GiB/4 CPU initial cap; persistent model volume. Use one loaded model/parallel request, an 8,192-token context cap and `OLLAMA_NO_CLOUD=1` local-only configuration [S32]. No host port is published. Ollama's Docker CPU/GPU deployment modes are documented [S29]. A process health check is not a model readiness check; run a separate capability probe after an explicit model pull. Weight/runtime memory must be measured on the selected hardware.

Resource caps are guardrails, not measured average usage. Their totals can exceed the template-mode budget because Ollama is optional. Plan approximately 25-40 GB disk for source, images, model caches and small datasets; captures/images may need more. Run capture and inference sequentially on a constrained machine. Record actual `docker stats` rather than claiming guaranteed performance.

## D3. Networks, volumes and secrets

Use `data_net` marked internal for PostgreSQL/Neo4j and backend connectivity. Use `app_net` for web/API/worker/Ollama. Database services join only `data_net`; web and Ollama never connect directly to it. The backend bridges both networks for application access. These networks reduce accidental exposure but do not constitute a full network-security product; the runtime may still have egress through app_net. Application code allowlists the local model endpoint and must not make external calls after explicit model prefetch.

Named volumes: `pg_data`, `neo4j_data`, `neo4j_logs`, `artifacts`, `staging`, `hf_cache`, `ollama_models`, `caddy_data`, and `caddy_config`. The API mounts artifacts read-only and staging read/write; worker mounts artifacts/staging read/write and HF cache read-only during normal execution. The one-shot `models` service mounts HF cache read/write. Gold labels are never in application volumes/images. The offline trainer/evaluator mounts a separate private gold directory only for explicit evaluation commands.

Development secret files live in ignored `.secrets/`: `postgres_admin_password`, `postgres_password`, `neo4j_auth` (exact `neo4j/password` format), and `operator_token`. `make secrets` generates strong random values, applies restrictive host permissions and explains that Compose bind-mounted secret permissions can differ by host. Application settings support `_FILE` reads and never log values. A limited `volume-init` utility may initialize ownership of named artifact/cache/staging volumes; it must not chown arbitrary host paths. Test UID/GID access on a fresh volume. Do not silently run the application as root to hide permission failures.

`.env` contains image references and non-secret tuning, not frontend secrets. Never put database passwords/tokens in `VITE_*`. No source bind mounts, Docker socket, privileged mode or host filesystem mounts in the release profile. Third-party container privilege requirements are separately documented; application containers run as UID/GID 10001 with no-new-privileges and dropped capabilities where compatible.

## D4. Image construction and dependency pinning

The backend Dockerfile has a dependency/build stage and a slim runtime stage. Copy `pyproject.toml`/`uv.lock` before source for caching; use `uv sync --frozen --no-dev --no-editable` with a tested CPU PyTorch source; copy the virtual environment and application into the runtime. Build and runtime must use the same Python baseline. No compiler, package-manager token, gold dataset, model cache or repository `.env` belongs in the runtime image. Do not download model weights in the Dockerfile.

The web Dockerfile builds with Node 24 LTS and `npm ci`, then copies only `dist/` and the Caddyfile into a minimal server image. Serve assets with immutable caching only for hashed asset paths; do not cache `index.html` permanently. Use a restrictive CSP compatible with the tested UI, `nosniff`, and same-origin routing. Build all images with commit/version labels and export image digests in the release manifest.

Phase 00 fills `.env` variables with registry-verified `repository:tag@sha256:...` values and records architecture. Empty required image variables fail fast; the blueprint deliberately contains no fabricated digests. Lock transitive dependencies, scan licenses/vulnerabilities and review upgrades explicitly. Use reproducible lockfiles, not unbounded `pip install -U` inside a release container.

## D5. Startup and model bootstrap

Compose `depends_on` alone is not readiness; use health checks and an explicit migration command [S23]. Target quickstart after implementation:

```bash
cp .env.example .env
make secrets
make versions-check
make build
docker compose up -d postgres neo4j
make volume-init
make migrate
make models-embeddings
make seed
make demo                 # template-first usable app
make models-llm           # explicit download into Ollama volume
make demo-llm             # app + llm profile, actual capability probe
```

The exact commands are implemented and tested by the phase prompts. `make demo` uses template generation; `make demo-llm` selects Ollama explicitly. The worker does not hard-depend on an optional profile, so missing Ollama cannot prevent template mode. Inference must not silently pull a model. Set HF offline/local-only flags after prefetch, pin Qwen tokenizer files, and test a disconnected runtime. Installation/download needs network; offline operation after prefetch is a separate claim that requires a test.

Compose profiles selectively start optional services [S26]. GPU use is an explicit overlay plus a verified host driver/toolkit, never mandatory. On macOS, native Ollama is an optional hardware-specific adapter after verifying current platform support; the CPU-only Docker path remains the portable reference. A host-native endpoint must be explicitly allowlisted and still never exposed through the public proxy.

## D6. Public portfolio deployment and rollback

The cheapest demonstration is a local recording plus a static portfolio page. A live read-only application can run on one user-owned machine/VM with precomputed results. Public mode disables imports, new model generation, job cancellation and training server-side. Display saved/replayed results honestly. A public server does not need Ollama running. No provider subscription or permanent GPU server is needed.

For a real live deployment, add a reviewed production Compose override with a domain, TLS through Caddy, host firewall rules and externally checked port exposure. Only 80/443 may be public. Keep all databases/model ports private. A TLS certificate, DNS record, external host and public publication need operator authorization; without them the public deployment gate is BLOCKED, not imaginary success. Cap logs and disk usage; do not expose metrics or debug endpoints.

Backups: pause imports/publication, create a transactionally consistent PostgreSQL dump, copy immutable artifacts and version manifests, verify hashes, then resume. Because graph snapshots are derived, rebuild Neo4j from the authoritative sources rather than assuming Community has Enterprise online backup. Restore into a clean project/volumes, run migration compatibility checks and investigate a saved fixture. Keep seven daily and four weekly backups as a proposed retention policy, subject to local disk limits. Never use `docker compose down -v` in normal stop/update instructions.

Rollout: build immutable images; backup; apply additive compatible migration; start updated app; probe health/evidence; switch traffic. Rollback uses the prior image and a compatible snapshot; a destructive schema downgrade requires explicit restoration and may lose newer writes. Test graceful worker cancellation, pending jobs and snapshot reconciliation. Commands must return failures rather than swallowing them.

## D7. Verification gates

Run `docker compose config --quiet`, build both app images, verify no secrets/gold in layers, run containers with fresh volumes, check every health/readiness boundary, perform a template and an actual local-model investigation, restart services, reclaim an expired job, rebuild a graph, and rehearse backup/restore. Scan host ports externally when publicly deployed. Record resource use and actual image/model digests. YAML parsing alone is only a static validation step, not proof these runtime tests passed.
