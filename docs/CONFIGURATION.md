# Configuration

`configs/defaults.yaml` is parsed with `yaml.safe_load` into strict Pydantic models. Unknown keys and out-of-range values fail at startup. `load_settings(path)` applies the validated defaults, then explicit `TRACECAUSE_*` environment overrides. This function does not read `.env` by itself; Docker Compose reads the non-secret `.env` file and passes selected values to containers. The runtime default is public read-only with template generation. A missing optional model does not prevent the foundation process from starting.

## Runtime environment variables

| Variable | Default / rule |
|---|---|
| `TRACECAUSE_PUBLIC_READ_ONLY` | `true`; `false` requires `TRACECAUSE_OPERATOR_TOKEN` or `_FILE` |
| `TRACECAUSE_GENERATION_PROVIDER` | `template`; `ollama` requires operator mode, explicit opt-in and a pinned model digest |
| `TRACECAUSE_ENABLE_LOCAL_MODEL` | `false`; set `true` only for local operator inference |
| `TRACECAUSE_OLLAMA_MODEL`, `TRACECAUSE_OLLAMA_MODEL_DIGEST`, `TRACECAUSE_OLLAMA_URL` | Reviewed Qwen tag; digest required for local inference; URL limited to `http://ollama:11434` or loopback port 11434 |
| `TRACECAUSE_EMBEDDING_MODEL`, `TRACECAUSE_EMBEDDING_REVISION`, `TRACECAUSE_EMBEDDING_DIMENSIONS` | MiniLM model, pinned revision and 384 dimensions; mismatches fail |
| `TRACECAUSE_ARTIFACT_ROOT`, `TRACECAUSE_STAGING_ROOT` | Absolute non-root, non-gold paths with no existing symlink component |
| `TRACECAUSE_PG_HOST`, `TRACECAUSE_PG_PORT`, `TRACECAUSE_PG_DATABASE`, `TRACECAUSE_PG_USER` | `postgres`, `5432`, `tracecause`, `tracecause_app`; port 1–65535 |
| `TRACECAUSE_PG_PASSWORD` or `_FILE` | Optional at contract load, required by the later database adapter; never set both |
| `TRACECAUSE_NEO4J_URI`, `TRACECAUSE_NEO4J_AUTH` or `_FILE` | `bolt://neo4j:7687`; URI must not embed credentials; file/direct secret are mutually exclusive |
| `TRACECAUSE_OPERATOR_TOKEN` or `_FILE` | Required when write/operator mode is enabled; never set both |
| `TRACECAUSE_QUESTION_CHARACTERS` | At most 2,000 |
| `TRACECAUSE_GRAPH_MAX_HOPS`, `_NODES`, `_EDGES` | At most 3, 80, 120 respectively |
| `TRACECAUSE_GENERATION_DEADLINE_SECONDS`, `_MAX_CONCURRENCY` | At most 180 seconds and exactly one active generation |
| `TRACECAUSE_GENERATION_CONTEXT_TOKENS`, `_INPUT_TOKENS`, `_OUTPUT_TOKENS` | At most 8,192, 6,000 and 1,000; input plus output must fit context |

YAML also validates all telemetry thresholds, chunking sizes, retrieval caps, heuristic weights, job leases and upload limits. The configuration model is a policy boundary; later phases must use these settings when implementing the corresponding operations.

File-backed secrets are read as UTF-8 and stripped of terminal whitespace. An empty file fails. An operator token is never copied into a frontend variable. The local model URL is an exact allowlist to prevent client-controlled provider destinations. No paid model API or pricing configuration is present; see ADR 0002.
