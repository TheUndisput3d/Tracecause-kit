# Deterministic investigation walkthrough

Phase 09 runs a cited investigation on real PostgreSQL and Neo4j data. It uses a versioned template, not a language model. The generated public fixture and authored demonstration runbooks are synthetic; no evaluator truth is read at runtime.

## Local walkthrough

From the repository root, use the pinned `uv` executable if the global version differs from `pyproject.toml`:

```bash
export PATH="/home/aayush/.cache/uv/archive-v0/02Rv7i6dB1RmEAjA/bin:$PATH"
make up
make migrate
make fixtures
make seed
make demo-cli
make eval-smoke
```

`make seed` imports public episode `03c9ef66-2609-5aec-86fa-9dda41e1c9af` idempotently, ingests the authored runbooks, materializes two event-time cutoffs, and publishes a graph snapshot. The artifact root is `reports/phase09-demo-artifacts`. `make demo-cli` prints the structured result; `make eval-smoke` investigates both cutoffs and reopens every returned citation. These commands need local database secrets created by `make secrets`. The PostgreSQL admin credential is used for this developer walkthrough; production role separation belongs to later phases.

For a directed support path in this fixture, use an observed symptom service key:

```bash
make demo-cli DEMO_SYMPTOM_KEY=b05172b8d20a6c8798e9ede21a75a11a7b23f58d97aa6ee2793700664c79d5fe
```

The key is a public fixture service identity supplied to the CLI. It is not a rule in the ranker. Read `investigation_id` and an `evidence_id` from the result, then inspect or export it:

```bash
uv run --locked tracecause investigation evidence \
  --investigation-id <UUID> --evidence-id <EVIDENCE_ID> \
  --artifact-root reports/phase09-demo-artifacts \
  --db-user postgres --password-file .secrets/postgres_admin_password \
  --neo4j-auth-file .secrets/neo4j_auth
uv run --locked tracecause investigation export \
  --investigation-id <UUID> --output-file reports/investigation-export.json \
  --artifact-root reports/phase09-demo-artifacts \
  --db-user postgres --password-file .secrets/postgres_admin_password
```

The CLI accepts a relative artifact root and resolves it against the current directory while retaining the artifact store's symlink checks. Export requires a new destination to prevent accidental overwrite.

## Decision rules

The service pins a READY snapshot in the caller's workspace and episode, recomputes signals from persisted spans for the requested cutoff, obtains scoped retrieval candidates, and traverses eligible CALLS edges. Candidate generation starts with anomalous services and an optional observed symptom, adds eligible neighbors within three hops, and caps the set at 20. It records truncation. Candidate generation and ranking never inspect evaluator labels or service names.

The `heuristic-v1` score is `0.30 L + 0.30 E + 0.15 T + 0.15 P + 0.10 C - 0.20 M`: latency deviation `L = max(0, z_latency)/10`, error excess `E = max(0, z_error)/10`, normalized earlier anomalous onset `T`, directed symptom proximity `P = 1/(1+hops)`, fraction of anomalous callers `C`, and missing feature fraction `M`. Scores are diagnostic ordering values, not probabilities. Ties use the service key for deterministic output. The top three receive direct observation support; eligible retrieved runbook checks and directed paths are added where available. The final bundle is capped at 12 evidence items, three paths, and 6000 evidence tokens. The Phase 11 CLI defaults to the promoted learned ranker; the Make targets in this Phase 09 walkthrough explicitly keep heuristic scoring. The trained model, abstention threshold, development comparison and feature-contribution citations are described in `MODEL_CARD.md`.

At the baseline boundary or with missing baseline/analysis samples, the result is `insufficient_evidence`. An unknown symptom is also insufficient. If adequate samples have no qualifying anomaly, the result is `no_anomaly_detected`. Otherwise the template lists up to three supported service hypotheses. The narrative uses only typed evidence already selected; it states that observed calls do not prove cause.

Before persistence, the validator rereads the eligible corpus and exact graph observations. It checks citation text and locators, scope, cutoff and availability, exact numeric metric value/unit/window, path direction and support, claim references, and recomputed ranks. A learned report also checks each standardized feature contribution against the runtime scorer and eligible observation references. An unsupported report is rejected. The result is stored in a content addressed artifact with the manifest, template, ranker, embedding and configuration provenance. A repeat request returns the saved result with `is_replayed=true`.

## Current limits

Event-time replay is supported for the synthetic fixture. Source-available replay rejects fixtures without trustworthy availability stamps. Neither the ranker nor the template proves a root cause; ranking quality has only been measured on the small synthetic development split. The CLI is the current interface; HTTP and dashboard work belong to later phases. The graph and PostgreSQL stores are not atomically committed together, so reconciliation remains necessary after interrupted publication.
