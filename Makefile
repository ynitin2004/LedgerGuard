# LedgerGuard AI task runner (Linux/macOS/CI/EC2).
# Windows equivalent with identical targets: ./scripts/dev.ps1 <target>
.DEFAULT_GOAL := help
.PHONY: help env install up down down-volumes ps logs migrate api test test-unit lint fmt typecheck check

help:  ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env:  ## Create .env from .env.example if missing
	@test -f .env || cp .env.example .env

install:  ## Install locked dependencies into .venv
	uv sync --frozen

up: env  ## Start local infrastructure and wait until healthy
	docker compose up -d --wait --wait-timeout 180

down:  ## Stop local infrastructure (data kept)
	docker compose down

down-volumes:  ## Stop local infrastructure and DELETE its data
	docker compose down -v

ps:  ## Show service health
	docker compose ps

logs:  ## Tail infrastructure logs
	docker compose logs -f --tail 100

migrate: env  ## Apply database migrations
	uv run python -m alembic -c migrations/alembic.ini upgrade head

api: env  ## Run the API with reload on http://127.0.0.1:8000
	uv run python -m uvicorn --factory apps.api.main:create_app --reload --host 127.0.0.1 --port 8000

test:  ## All tests (integration tests need Docker)
	uv run python -m pytest

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
