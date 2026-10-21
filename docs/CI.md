# CI and local verification

The pinned workflows are `.github/workflows/ci.yaml` and `manual-verification.yaml`.
No GitHub workflow run is claimed: these commands were exercised locally. Publishing
or pushing this repository requires the owner's permission.

## Ordinary checks

Use uv0.11.19, Python3.12.13 and Node24.21.0. The uv release catalog did not supply
3.12.15 during verification; the separately pinned Docker Python image is verified
by the container release gate. Install with `uv sync --locked --group ml` and
`npm ci --prefix apps/web`. Model prefetch is an explicit setup step:
`uv run --group ml python -m scripts.ci.prefetch` downloads only the pinned Qwen
tokenizer. `--embeddings` additionally obtains pinned MiniLM weights for the real
embedding integration job. No Qwen generation weights or paid provider are used.

`UV_NO_SYNC=1 make check` runs lint, formatting, types, schema parity, unit/property/
contract checks, frontend tests and build. `make test-integration` requires actual
PostgreSQL and Neo4j; absent dependencies fail the gate. Set up `.env` from
`.env.example`, run `make secrets`, and start `docker compose up -d --wait postgres
neo4j`. `make test-fixture-evaluation` creates deterministic public fixtures and
runs two cutoff investigations with all citations opened. This is a functional
fixture evaluation, not another held-out research run. `make test-browser` owns a
seeded API/worker/web lifecycle; ports15432,17687,18080,15173 must be available.

`UV_NO_SYNC=1 make release-check` composes these checks, actual MiniLM and secret/
dependency scans. It does not start database containers for you. The ordinary
workflow starts real database containers explicitly, then stops them on exit.
Tests use synthetic fixtures; their artifact paths remain local.

## Explicit heavy gates

`make performance-check` requires the seeded local dashboard and administrator
access to create new isolated performance databases. It records reports under a
new timestamped directory and applies `configs/performance-budgets.json`.
Manual workflow dispatch is restricted to the main branch on an operator-prepared
`tracecause-verified` runner, with existing dependencies, secrets, dashboard and
model weights. It is never triggered by pull requests. Actual local model browser
verification uses `npm --prefix apps/web run test:e2e:llm`. Full capture remains an
explicit operator workflow described in `CAPTURE.md`; never run it over the frozen
research output roots. Capture and model checks are excluded from ordinary CI.

## Scanners and inventory

`make security-scan` retains pip-audit2.10.1, npm audit and gitleaks8.30.1 reports
and fails on scanner errors or findings. Gitleaks binary archive SHA is checked
before use. Its source allowlist contains four exact public model/tokenizer/
service hashes. Six exact historical fingerprints in `.gitleaksignore` were
reviewed as public hashes, not credentials. No entire source/test directory is
excluded. Both Git history and current tracked/unignored files are scanned.
`uv run python scripts/generate_inventory.py` regenerates license inventories.
Action SHAs and downloaded scanner versions are recorded in the phase17 report.
Custom runner labels are declared in `.github/actionlint.yaml`.

## Clean environment scope

`scripts.ci.clean_checkout --destination NEW_PATH` creates a local source checkout
with history and a source hash manifest, without ignored dependencies, secrets,
fixtures or reports. Use a new virtual environment and `npm ci`, generate new
secrets, and start a distinct Compose project with new volumes. Download caches
may be reused; they are not fresh runtime/database state. Never run two database
projects on the same published ports. Stop the owned development stack first and
restore it afterward; retain old volumes. No destructive cleanup is part of this
procedure.
