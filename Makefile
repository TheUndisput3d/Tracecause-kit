.PHONY: bootstrap build check contract fixtures fixtures-smoke fixtures-validate ingestion-sweep test-unit test-contract test-integration test-embedding-integration secrets up down worker-up worker-down migrate snapshot-build graph-audit doctor db-check web-check seed demo-cli eval-smoke models-llm models-probe demo-llm capture-doctor capture-status capture-smoke capture capture-audit capture-review-validate capture-review-gate capture-reset capture-stop questions-draft questions-review-validate ranker-test graph-reconstruction-test

capture-doctor:
	uv run --locked python -m scripts.capture.minishop doctor

capture-status:
	uv run --locked python -m scripts.capture.minishop status

capture-smoke:
	uv run --locked python -m scripts.capture.minishop smoke

capture:
	uv run --locked python -m scripts.capture.minishop full

capture-audit:
	uv run --locked python -m scripts.capture.audit

capture-review-validate:
	uv run --locked python -m scripts.capture.review

.PHONY: capture-review-packet
capture-review-packet: capture-audit
	uv run --locked python -m scripts.capture.packet

capture-review-gate:
	uv run --locked python -m scripts.capture.review --require-representative

.PHONY: capture-review-evidence capture-ai-review-validate capture-ai-review-gate
capture-review-evidence:
	uv run --locked python -m scripts.capture.evidence

capture-ai-review-validate:
	uv run --locked python -m scripts.capture.review --reviewer-kind ai --sheet reports/capture/ai_review_sheet.csv --output reports/capture/ai_review_validation.json

capture-ai-review-gate:
	uv run --locked python -m scripts.capture.review --reviewer-kind ai --sheet reports/capture/ai_review_sheet.csv --output reports/capture/ai_review_validation.json --require-representative --require-all-test

capture-reset:
	uv run --locked python -m scripts.capture.minishop reset

capture-stop:
	$(MAKE) capture-reset
	docker compose -f compose.capture.yaml stop

questions-draft:
	uv run --locked python -m scripts.evaluation.questions

questions-review-validate:
	uv run --locked python -m scripts.evaluation.review

ranker-test:
	uv run --locked --group ml python -m scripts.evaluation.ranker_test

graph-reconstruction-test:
	uv run --locked python -m scripts.evaluation.graph_reconstruction --split test

DEMO_EPISODE_ID ?= 03c9ef66-2609-5aec-86fa-9dda41e1c9af
DEMO_ARTIFACT_ROOT ?= $(CURDIR)/reports/phase09-demo-artifacts
RANKER_OUTPUT_ROOT ?= reports/phase11-training

bootstrap:
	uv sync --locked
	npm install --prefix .tools --save-exact --no-audit --no-fund node@24.21.0
	cd apps/web && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm ci

build:
	set -a; . ./.env; set +a; docker build -f Dockerfile.backend --build-arg PYTHON_IMAGE="$$PYTHON_IMAGE" --build-arg UV_IMAGE="$$UV_IMAGE" -t tracecause-backend:phase00 .
	set -a; . ./.env; set +a; docker build -f Dockerfile.web --build-arg NODE_IMAGE="$$NODE_IMAGE" --build-arg CADDY_IMAGE="$$CADDY_IMAGE" -t tracecause-web:phase00 .

contract:
	uv run --locked python scripts/export_contracts.py
	cd apps/web && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm run generate:api

fixtures:
	uv run --locked python -m scripts.fixtures --seed 20261010
	uv run --locked python -m scripts.validate_fixtures --public-root data/generated/seed-20261010 --gold-root data/gold/seed-20261010

fixtures-smoke:
	uv run --locked python -m scripts.fixtures --smoke --seed 20261010
	uv run --locked python -m scripts.validate_fixtures --public-root data/fixtures/smoke --gold-root data/gold/smoke

fixtures-validate:
	uv run --locked python -m scripts.validate_fixtures --public-root data/generated/seed-20261010 --gold-root data/gold/seed-20261010

ingestion-sweep:
	uv run --locked python -m scripts.validate_ingestion --public-root data/generated/seed-20261010

check:
	uv run --locked ruff check src tests scripts
	uv run --locked ruff format --check src tests scripts
	uv run --locked mypy
	uv run --locked python scripts/export_contracts.py --check
	uv run --locked python scripts/check_frontend_types.py
	uv run --locked pytest tests/unit tests/contract
	cd apps/web && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm run lint && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm run typecheck && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm test && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm run build

test-unit:
	uv run --locked pytest tests/unit

test-contract:
	uv run --locked pytest tests/contract

test-integration: fixtures
	uv run --locked --group ml pytest tests/integration

test-embedding-integration:
	uv run --locked --group ml pytest tests/model/test_minilm.py

secrets:
	python3 scripts/make_secrets.py

up:
	docker compose up -d --wait postgres neo4j

down:
	docker compose stop worker postgres neo4j

worker-up:
	docker compose up -d worker

worker-down:
	docker compose stop worker

migrate:
	uv run --locked tracecause db migrate --password-file .secrets/postgres_admin_password
	uv run --locked tracecause graph migrate --neo4j-auth-file .secrets/neo4j_auth

snapshot-build:
	@test -n "$(EPISODE_ID)" || { echo 'EPISODE_ID is required' >&2; exit 2; }
	uv run --locked tracecause graph build --episode-id "$(EPISODE_ID)" --password-file .secrets/postgres_password --neo4j-auth-file .secrets/neo4j_auth $(if $(ARTIFACT_ROOT),--artifact-root "$(ARTIFACT_ROOT)")

graph-audit:
	uv run --locked tracecause graph audit --password-file .secrets/postgres_password --neo4j-auth-file .secrets/neo4j_auth

doctor:
	uv run --locked tracecause doctor

db-check:
	python3 scripts/db_probe.py

web-check:
	cd apps/web && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm run lint && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm run typecheck && PATH="$(CURDIR)/.tools/node_modules/node/bin:$$PATH" npm run build

seed:
	uv run --locked tracecause investigation seed --episode-dir data/generated/seed-20261010/$(DEMO_EPISODE_ID) --corpus-dir data/runbooks/demo --artifact-root "$(DEMO_ARTIFACT_ROOT)" --db-user postgres --password-file .secrets/postgres_admin_password --neo4j-auth-file .secrets/neo4j_auth

demo-cli:
	uv run --locked tracecause investigation run --ranking-mode heuristic --episode-id "$(DEMO_EPISODE_ID)" --artifact-root "$(DEMO_ARTIFACT_ROOT)" --db-user postgres --password-file .secrets/postgres_admin_password --neo4j-auth-file .secrets/neo4j_auth $(if $(DEMO_SYMPTOM_KEY),--symptom-service-key "$(DEMO_SYMPTOM_KEY)")

eval-smoke:
	uv run --locked tracecause investigation smoke --ranking-mode heuristic --episode-id "$(DEMO_EPISODE_ID)" --artifact-root "$(DEMO_ARTIFACT_ROOT)" --db-user postgres --password-file .secrets/postgres_admin_password --neo4j-auth-file .secrets/neo4j_auth $(if $(DEMO_SYMPTOM_KEY),--symptom-service-key "$(DEMO_SYMPTOM_KEY)")

models-llm:
	uv run --locked tracecause models pull --artifact-root "$(DEMO_ARTIFACT_ROOT)"

models-probe:
	uv run --locked tracecause models probe --artifact-root "$(DEMO_ARTIFACT_ROOT)"

demo-llm:
	uv run --locked tracecause investigation run --ranking-mode heuristic --generation-mode ollama --episode-id "$(DEMO_EPISODE_ID)" --artifact-root "$(DEMO_ARTIFACT_ROOT)" --db-user postgres --password-file .secrets/postgres_admin_password --neo4j-auth-file .secrets/neo4j_auth $(if $(DEMO_SYMPTOM_KEY),--symptom-service-key "$(DEMO_SYMPTOM_KEY)")

train: fixtures
	uv run --locked tracecause ranking train --public-root data/generated/seed-20261010 --gold-root data/gold/seed-20261010 --output-root "$(RANKER_OUTPUT_ROOT)"

dev-report:
	uv run --locked tracecause ranking report --output-root "$(RANKER_OUTPUT_ROOT)"

ranker-register:
	uv run --locked tracecause ranking register --output-root "$(RANKER_OUTPUT_ROOT)" --artifact-root "$(DEMO_ARTIFACT_ROOT)" --db-user postgres --password-file .secrets/postgres_admin_password

ranker-register-bundled:
	$(MAKE) ranker-register RANKER_OUTPUT_ROOT=models/candidate-logistic-v4

demo-ranker:
	uv run --locked tracecause investigation run --ranking-mode learned --episode-id "$(DEMO_EPISODE_ID)" --artifact-root "$(DEMO_ARTIFACT_ROOT)" --db-user postgres --password-file .secrets/postgres_admin_password --neo4j-auth-file .secrets/neo4j_auth $(if $(DEMO_SYMPTOM_KEY),--symptom-service-key "$(DEMO_SYMPTOM_KEY)")

.PHONY: evaluation-claims evaluation-results evaluation-captured-prepare evaluation-captured-smoke evaluation-captured

evaluation-claims:
	uv run --locked python -m scripts.evaluation.claim_review

evaluation-results:
	uv run --locked python -m scripts.evaluation.render_report

.PHONY: evaluation-graph-projection
evaluation-graph-projection:
	uv run --locked python -m scripts.evaluation.graph_projection

.PHONY: evaluation-captured-pairs
evaluation-captured-pairs:
	uv run --locked python -m scripts.evaluation.captured_pairs

evaluation-captured-prepare:
	uv run --locked python -m scripts.evaluation.captured_prepare

evaluation-captured-smoke:
	uv run --locked --group ml python -m scripts.evaluation.captured_benchmark --smoke

evaluation-captured:
	uv run --locked --group ml python -m scripts.evaluation.captured_benchmark

.PHONY: test-browser test-security security-scan release-check performance-check

test-browser:
	uv run --locked --group ml python -m scripts.ci.browser

test-security:
	uv run --locked --group ml pytest tests/unit/test_security_recovery.py tests/unit/test_generation.py tests/unit/test_artifacts.py tests/unit/test_ranker.py tests/integration/test_api.py

security-scan:
	uv run --locked --group ml python -m scripts.ci.scans

test-fixture-evaluation:
	$(MAKE) fixtures
	$(MAKE) migrate
	$(MAKE) seed
	$(MAKE) eval-smoke

release-check: check test-integration test-embedding-integration test-fixture-evaluation test-browser security-scan

performance-check:
	uv run --locked --group ml python -m scripts.performance.check

RELEASE_COMPOSE = docker compose --env-file .env.release -f compose.release.yaml
.PHONY: release-prepare release-build release-up release-stop release-models demo test-e2e
release-prepare:
	uv run --locked --group ml python -m scripts.deployment.prepare

RELEASE_VERSION ?= local
release-build: release-prepare
	uv run --locked --group ml python -m scripts.deployment.build --version "$(RELEASE_VERSION)"

release-models:
	$(RELEASE_COMPOSE) run --rm models

release-up:
	$(RELEASE_COMPOSE) up -d --wait postgres neo4j
	$(RELEASE_COMPOSE) run --rm volume-init
	$(RELEASE_COMPOSE) run --rm migrate
	$(MAKE) release-models
	$(RELEASE_COMPOSE) run --rm seed
	$(RELEASE_COMPOSE) up -d --wait api web

release-stop:
	$(RELEASE_COMPOSE) --profile operator --profile llm stop

demo: release-up

test-e2e: test-browser

.PHONY: backup restore-check
backup:
	@test -n "$(BACKUP_DIR)" || { echo 'Set BACKUP_DIR to a new local directory' >&2; exit 2; }
	uv run --locked --group ml python -m scripts.deployment.backup --output "$(BACKUP_DIR)"

restore-check:
	@test -n "$(BACKUP_DIR)" -a -n "$(RESTORE_DB)" -a -n "$(POSTGRES_CONTAINER)" -a -n "$(RESTORE_REPORT)" || { echo 'Set BACKUP_DIR RESTORE_DB POSTGRES_CONTAINER RESTORE_REPORT and isolated TRACECAUSE settings' >&2; exit 2; }
	uv run --locked --group ml python -m scripts.recovery.restore --backup "$(BACKUP_DIR)" --database "$(RESTORE_DB)" --postgres-container "$(POSTGRES_CONTAINER)" --report "$(RESTORE_REPORT)"

.PHONY: eval
# The caller must choose a new output root; frozen research outputs are never overwritten.
eval:
	@test -n "$(EVAL_OUTPUT_ROOT)" -a -n "$(EVAL_PREPARED_ROOT)" -a -n "$(EVAL_REVIEW)" || { echo 'Set new EVAL_OUTPUT_ROOT plus reviewed EVAL_PREPARED_ROOT and EVAL_REVIEW' >&2; exit 2; }
	@test ! -e "$(EVAL_OUTPUT_ROOT)" || { echo 'Evaluation output must be new' >&2; exit 2; }
	uv run --locked --group ml python -m scripts.evaluation.benchmark --prepared-root "$(EVAL_PREPARED_ROOT)" --review "$(EVAL_REVIEW)" --output-root "$(EVAL_OUTPUT_ROOT)"

# Complete operator application, including all local AI services; Docker is the only prerequisite.
FULL_COMPOSE = docker compose -f compose.full.yaml
.PHONY: docker-up docker-stop docker-logs docker-status docker-token
docker-up:
	$(FULL_COMPOSE) up --build -d --wait --wait-timeout 2400

docker-stop:
	$(FULL_COMPOSE) stop

docker-logs:
	$(FULL_COMPOSE) logs -f --tail 100

docker-status:
	$(FULL_COMPOSE) ps --all

docker-token:
	$(FULL_COMPOSE) exec -T api cat /run/secrets/operator_token

.PHONY: train-transfer
train-transfer:
	@test -n "$(TRANSFER_OUTPUT_ROOT)" || { echo 'Set a new TRANSFER_OUTPUT_ROOT' >&2; exit 2; }
	uv run --locked --group ml python -m scripts.evaluation.train_transfer --output "$(TRANSFER_OUTPUT_ROOT)"
