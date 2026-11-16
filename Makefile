# LedgerGuard AI task runner (Linux/macOS/CI/EC2).
# Windows equivalent with identical targets: ./scripts/dev.ps1 <target>
.DEFAULT_GOAL := help
.PHONY: help env install up down down-volumes ps logs db-role migrate api worker-janitor api-client train register-model seed verify-decisions image bench-up workers-up reconcile replay-dlq load test test-linux test-unit lint fmt typecheck check

help:  ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env:  ## Create .env from .env.example if missing
	@test -f .env || cp .env.example .env

install:  ## Install locked dependencies into .venv
	uv sync --frozen

up: env  ## Start local infrastructure and wait until healthy
	docker compose up -d --wait --wait-timeout 180

down:  ## Stop local infrastructure (data kept)
	docker compose --profile bench down

down-volumes:  ## Stop local infrastructure and DELETE its data
	docker compose --profile bench down -v

ps:  ## Show service health
	docker compose ps

logs:  ## Tail infrastructure logs
	docker compose logs -f --tail 100

db-role: env  ## Give the runtime DB role a login (only needed for volumes created before M1)
	docker compose exec -T postgres sh /docker-entrypoint-initdb.d/10-app-role.sh

migrate: env  ## Apply database migrations
	uv run python -m alembic -c migrations/alembic.ini upgrade head

api: env  ## Run the API with reload on http://127.0.0.1:8000
	uv run python -m uvicorn --factory apps.api.main:create_app --reload --host 127.0.0.1 --port 8000

worker-janitor: env  ## Run the janitor worker (purges expired idempotency keys)
	uv run python -m apps.worker janitor

api-client: env  ## Create an API client: make api-client NAME=demo
	uv run python scripts/create_api_client.py --name $(or $(NAME),demo)

train:  ## Train the risk model on synthetic data -> models/lr-v1.json (needs numpy)
	uv run python scripts/train_model.py

register-model: env  ## Register models/lr-v1.json and make it active (schema owner)
	uv run python scripts/register_model.py models/lr-v1.json --activate

seed: env  ## Seed 30 days of synthetic history for a new client: make seed NAME=demo
	uv run python scripts/seed_history.py --name $(or $(NAME),demo)

verify-decisions: env  ## Replay every stored decision; fails on any mismatch
	uv run python scripts/verify_decisions.py

image:  ## Build the app image (API + workers)
	docker build -f infra/api/Dockerfile -t ledgerguard-app:local .

bench-up: env image  ## Containerised API (4 workers) on 127.0.0.1:8001 + event workers
	docker compose --profile bench up -d --no-build --wait api outbox-publisher feature-updater

workers-up: env image  ## Start the event workers (outbox-publisher, feature-updater)
	docker compose --profile workers up -d --no-build outbox-publisher feature-updater

reconcile: env  ## Outbox vs topic vs inbox counts (fails if not reconciled)
	docker compose --profile workers run --rm --no-deps feature-updater /opt/venv/bin/python -m apps.worker reconcile --strict

replay-dlq: env  ## Replay dead-lettered events: make replay-dlq EVENT=<id>  (or EVENT=all)
	docker compose --profile workers run --rm --no-deps feature-updater /opt/venv/bin/python -m apps.worker replay-dlq $(if $(filter all,$(EVENT)),--all,--event-id $(EVENT))

load: env  ## k6 hot-path load test: LOAD_API_KEY=<key from make seed> make load
	docker compose --profile bench run --rm --no-deps k6 run --summary-export /load/results/latest.json /load/transactions.js

test:  ## All tests (integration tests need Docker)
	uv run python -m pytest

test-linux:  ## Full test suite inside Linux (hosts where Kafka/numpy wheels cannot load)
	docker build -q -t ledgerguard-ml:local infra/ml
	docker run --rm -v "$$PWD:/repo" -v ledgerguard-uv-cache:/root/.cache/uv -v /var/run/docker.sock:/var/run/docker.sock -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal -e TESTCONTAINERS_RYUK_DISABLED=true ledgerguard-ml:local uv run --frozen python -m pytest -p no:cacheprovider

test-unit:  ## Unit tests only (no Docker)
	uv run python -m pytest -m "not integration"

lint:  ## Ruff lint + format check
	uv run python -m ruff check .
	uv run python -m ruff format --check .

fmt:  ## Apply Ruff fixes and formatting
	uv run python -m ruff check --fix .
	uv run python -m ruff format .

typecheck:  ## mypy (strict)
	uv run python -m mypy

check: lint typecheck test  ## Everything CI runs
