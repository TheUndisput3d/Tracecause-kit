# Dependency inventory and version-resolution contract

## Selection policy

Use the named packages below. These are implementation choices, not claims that all package versions are interchangeable. Phase 00 resolves maintained compatible versions from primary package/project documentation, locks transitive dependencies, records licenses and runs import/build probes. Do not fabricate a lockfile or copy an arbitrary latest release into every constraint. Python 3.12, PostgreSQL 17, React 19 and Node 24 LTS are deliberate starting baselines; document any compatibility-driven change in an ADR.

The repository must contain `uv.lock`, `apps/web/package-lock.json`, `docs/VERSIONS.md`, `docs/THIRD_PARTY.md` and a generated dependency/license inventory. Container images use verified tags plus full digests for the selected CPU architecture. Model weights/tokenizers use exact revisions/digests. A model tag alone is not a reproducibility record.

## Python application group

`fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `sqlalchemy`, `psycopg[binary]`, `alembic`, `neo4j`, `numpy`, `scikit-learn`, `sentence-transformers`, CPU-compatible `torch`, `transformers` for the matching Qwen tokenizer, `huggingface-hub` for explicit model prefetch, `httpx`, `structlog`, `prometheus-client`, `PyYAML`, `markdown-it-py`, and `python-multipart`.

Use the official Neo4j driver directly. Use HTTPX for Ollama's native HTTP interface; no OpenAI SDK is needed. Explicitly test a supported CPU PyTorch wheel/index and record the source. Do not install a CUDA stack just to generate CPU embeddings. Import heavy models lazily in worker modules so thin API startup does not load weights. Keep optional generation tokenization offline after prefetch.

## Python development group

`pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `respx`, and `jsonschema`. Add only required type-stub packages after checking the selected versions. Use real PostgreSQL/Neo4j Compose services for integration tests rather than requiring a second container-orchestration testing framework. A dependency scanner and secret scanner can be separate CI tools, pinned independently.

## Capture-only group

`opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi`, and `opentelemetry-instrumentation-httpx`, plus FastAPI/Uvicorn/HTTPX already used by the small service image. The instrumentor packages have their own coordinated release numbering; resolve compatible SDK/instrumentation releases rather than giving every package the same version string. Collector-contrib is a separately pinned image with the file exporter included.

## Frontend runtime packages

`react`, `react-dom`, `react-router-dom`, `@tanstack/react-query`, `tailwindcss` with its version-compatible Vite integration, the specifically used `@radix-ui/react-*` primitives (dialog, tabs, select, tooltip and switch), `lucide-react`, `cytoscape`, `recharts`, `zod`, `react-markdown`, and `rehype-sanitize`.

Do not add a second component library, an unmaintained graph wrapper or a state-management framework by habit. React local state, URL state and TanStack Query have distinct ownership. Style directly with the chosen Tailwind version and CSS variables; do not mix incompatible Tailwind configuration recipes.

## Frontend development packages

`typescript`, `vite`, `@vitejs/plugin-react`, `@types/react`, `@types/react-dom`, `@types/cytoscape`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, `msw`, `@playwright/test`, `@axe-core/playwright`, `openapi-typescript`, and compatible ESLint/typescript-eslint packages. Use only test-time MSW interception; the actual demonstration talks to the real API.

## Infrastructure and model artifacts

Infrastructure: Docker Engine plus Compose v2, PostgreSQL 17, a supported Neo4j Community release, Caddy, Ollama, a slim Python 3.12 image, Node 24 build image, uv build image, and optional Collector-contrib capture image. Use the Alpine Caddy variant only after verifying its non-root user-creation commands; use a Debian-based slim Python image for the supplied backend Dockerfile. GPU support is an optional deployment overlay with separately verified host prerequisites.

Models: `sentence-transformers/all-MiniLM-L6-v2` embeddings and `Qwen/Qwen3-4B-Instruct-2507` generation through the quantized `qwen3:4b-instruct-2507-q4_K_M` Ollama tag. MiniLM and Qwen model cards identify Apache-2.0 licenses [S07, S17]. Ollama is MIT-licensed software [S20]. Neo4j Community is GPLv3, not MIT; review its separate redistribution obligations [S21]. Docker Engine and Docker Desktop are different products with different licensing contexts [S24, S25]. No paid runtime model API is required.

## Explicit exclusions

No mandatory LangChain, LlamaIndex, Microsoft GraphRAG, GNN framework, vector database, Redis, Celery, Kafka, Kubernetes, Airflow, Spark, MLflow server, paid monitoring service or managed cloud database. A future extension needs a measured bottleneck or clear experiment, not a longer technology list.
