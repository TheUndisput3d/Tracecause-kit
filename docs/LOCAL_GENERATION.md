# Local structured generation

Local Qwen optionally selects facts for an investigation summary. Template mode
remains the default. Production uses `evidence-plan-v1`: the model receives the
question and a catalog of verified, cutoff-eligible claims, and chooses a focus
plus up to three fact IDs. Application code supplies the exact prose, values and
citations. It has no tools, database connection, shell access or evaluator truth.
The UI identifies AI fact selection and server rendering explicitly. For automatic
Docker startup of all components, see [DOCKER_FULL_STACK](DOCKER_FULL_STACK.md).

## Operator commands

With the local Ollama service listening on loopback and `.secrets/operator_token` present:

```bash
make models-llm      # explicit Qwen model and tokenizer prefetch
make models-probe    # report process, model, digest and JSON-schema capability
make seed            # if the public fixture is not already imported
make demo-llm
```

`models-llm` is an explicit download command; an investigation never pulls weights. The model tag is `qwen3:4b-instruct-2507-q4_K_M` and the reviewed Ollama manifest digest is `sha256:0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`. The Qwen tokenizer is pinned to Hugging Face revision `cdbee75f17c01a7cc42f958dc650907174af0554` and loaded locally at inference time. The CLI accepts only the loopback Ollama endpoint or the internal Compose service endpoint. A client cannot select an arbitrary model or URL. The optional Compose `llm` profile defines an internal Ollama service without a published port; its persistent model volume requires a separate explicit pull.

For a specific episode and cutoff:

```bash
uv run --locked tracecause investigation run \
  --generation-mode ollama \
  --episode-id 03c9ef66-2609-5aec-86fa-9dda41e1c9af \
  --as-of-ns 1767233400000000000 \
  --artifact-root reports/phase09-demo-artifacts \
  --db-user postgres --password-file .secrets/postgres_admin_password \
  --neo4j-auth-file .secrets/neo4j_auth
```

The CLI result labels `generation_origin` as `ollama` only after schema and support validation. On failure it returns a cited template with `generation_origin=template`, `fallback_reason` and an `ollama_template_fallback` warning. A saved result is immutable and a matching successful model request replays without inference.

## Bounds and validation

The limits are 8,192 context tokens, 6,000 input tokens, at most192 output tokens
across two attempts, one active model slot, and a180-second deadline. The durable
slot remains fenced after transport uncertainty; no automatic retry starts a
possibly overlapping generation. The pinned tokenizer counts messages before the
call; actual Ollama token counts are stored with the result. The current prompt is
`prompts/runtime/plan_v1.txt`; `/api/chat` uses a request-specific JSON Schema in
`format`, `stream:false`, deterministic temperature and no tools.

Both generation modes can expose up to three supported hypotheses. The model can
only select existing facts; an independent compiler checks membership, uniqueness
and focus compatibility. Status, candidate order, support, claims, exact metrics,
paths and limitations come from the server. The final validator recompiles the
plan and compares the narrative exactly, then rereads authoritative sources and
validates scope, values and cutoff. Numeric summary prose is permitted only when
it exactly matches this trusted compiler output. Raw documents and log text are
not included in the model plan request.

An invalid plan may receive one targeted repair without reflecting rejected text.
Timeout, unavailable model, changed digest, invalid repaired plan and output
overrun remain visible failures with a labeled template. Successful plan output
cannot rewrite factual values or citations, but the model can still choose a
less relevant focus. Neither source validation nor fact selection establishes
causation or production usefulness. Historical full-narrative copying is retained
only in `LegacyOllamaProvider` for explicit offline comparisons; the phase10 report
preserves its original behavior and measurements. See [ADR0015](adr/0015-evidence-plans-and-transfer-ranker.md)
and [actual verification](phase-reports/reliability.md).
