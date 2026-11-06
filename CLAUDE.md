# LedgerGuard AI — instructions for Claude Code (and Codex, via AGENTS.md)

## ⚠ ONE-TIME: new-machine setup (do this first, then delete this whole section)
The first PC had tools blocked or missing. On a new PC, before any milestone work:
1. Check the prerequisites and report the versions: `git`, `uv` (≥0.11), `docker` + `docker compose`. Python 3.12 comes via
   uv (`.python-version`); don't install it system-wide.
2. Tools that weren't installed on the first PC. Offer to install each one, and ask the user first (system install):
   - `gh` (GitHub CLI): watch CI with `gh run watch`. The first PC polled the REST API instead.
   - `make` (Linux/macOS usually have it): optional. `scripts/dev.ps1` has the same targets on Windows.
   - `actionlint`: optional, lints `.github/workflows/*.yml` (`docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint`).
   - **Not** k6: it runs through the `grafana/k6` Docker image from M2 on (ADR-010). Never install `httpx2`; tests use `httpx`.
3. Workarounds from the first PC. Keep them, they work everywhere: tools run as `uv run python -m <tool>`, because Windows
   Application Control blocked `.venv\Scripts\*.exe` (os error 4551, ADR-016). Host URLs use `127.0.0.1` (ADR-009).
   Postgres is on 55432, because 5432 was taken.
4. Verify: `uv sync`, `cp .env.example .env` (`dev.ps1 up` does this), `up`, `migrate`, `lint`, `typecheck`, `test`. All must
   pass. Then `curl http://127.0.0.1:8000/readyz` with the API running should return `"status":"ready"`.
5. Set the git identity repo-locally: `user.name=ynitin2004`, `user.email=ynitin2004@users.noreply.github.com`.
6. When steps 1–5 pass, **delete this section** from CLAUDE.md and the matching line in AGENTS.md. Commit as
   `chore: complete one-time machine setup`.

## Identity
Simulated payment-risk platform. Financial state is **deterministic**: rules + versioned ML + explicit policy
decide each transaction in one atomic Postgres transaction with a transactional outbox and idempotent
consumers. AI (investigation summaries, later RAG) is **advisory only**; it never changes a decision or sits on the hot path.

## Source of truth
- *Final Project Plan v2* supersedes v1. Its settled decisions are not revisited without a written proposal.
- Docs live in `docs/`: start with TASKS.md (status), ARCHITECTURE.md, DECISIONS.md (ADRs), DATA_MODEL.md, API_SPEC.md.
- Work only on the current milestone in TASKS.md. Do not start the next one without the user's approval.

## Stack
Python 3.12 (uv workspace) · FastAPI · SQLAlchemy 2 async + asyncpg · Alembic · Postgres 16 (pgvector image;
Supabase hosted) · Redis · Redpanda (confluent-kafka) · scikit-learn · AgentRouter (+fastembed in CI) ·
pytest/Hypothesis/Testcontainers/k6 (Docker) · OpenTelemetry → Collector → Prometheus → Grafana · Docker Compose ·
GitHub Actions · Next.js dashboard (M9) · AWS EC2 (M10).

## Architectural boundaries
- `domain/` is pure: no I/O, no framework or infrastructure imports.
- `application/` use cases depend on repository **protocols**; infrastructure implements them.
- Routers translate HTTP ↔ use cases only. Resources are wired in `create_app()` lifespan / `apps/api/deps.py`.
- Meaningful abstractions only. No DI frameworks, no speculative layers, no placeholder modules.

## Engineering standards
- Full type hints; `mypy --strict` must pass. Pydantic validates every external input.
- SQLAlchemy 2.x style (`select()`, `Mapped[]`), explicit transactions and Unit of Work; never autocommit business writes.
- Money = `amount_minor BIGINT` + `currency CHAR(3)`. Never floats for authoritative amounts.
- Persist rules, model and thresholds versions on every decision. Events are at-least-once; consumers dedupe via inbox.
- Migrations: reviewed autogenerate, expand → contract, `upgrade`/`downgrade` round trip must pass.
- Structured logs (structlog) with `correlation_id`; never log secrets, keys or prompts with evidence.
- No hardcoded secrets (`.env` only, git-ignored). Use `127.0.0.1`, not `localhost`, in host URLs (ADR-009).
- Never fabricate results: BENCHMARK.md and MODEL_CARD.md only hold numbers produced by repo scripts.

## Workflow
1. Read the relevant docs and the TASKS.md item; restate the task and acceptance before coding.
2. Implement in small steps; add or adjust tests for meaningful behaviour.
3. Run lint, typecheck and tests; fix what you broke. Report actual output, including failures.
4. Update docs (mark Planned vs Implemented) and TASKS.md. Record new decisions as ADRs.
5. Ask before system installs, deleting data, changing settled architecture or adding paid services.

## Commands (verified on Windows 11; `make <target>` is equivalent on Linux/CI)
```powershell
uv sync                                    # install locked deps into .venv
./scripts/dev.ps1 up                       # docker compose up -d --wait (creates .env if missing)
./scripts/dev.ps1 migrate                  # alembic -c migrations/alembic.ini upgrade head
./scripts/dev.ps1 api                      # uvicorn --factory apps.api.main:create_app --reload :8000
./scripts/dev.ps1 test                     # pytest (unit + Testcontainers integration; needs Docker)
./scripts/dev.ps1 test-unit                # pytest -m "not integration"
./scripts/dev.ps1 lint                     # ruff check + ruff format --check
./scripts/dev.ps1 typecheck                # mypy (strict)
./scripts/dev.ps1 down                     # stop stack (data kept); down-volumes deletes data
```
New migration: `uv run python -m alembic -c migrations/alembic.ini revision --autogenerate -m "msg"`, then review it.

## Local ports
API 8000 · Postgres **55432** · Redis 6379 · Redpanda 19092 · Console 8080 · Prometheus 9090 · Grafana 3000 · OTLP 4317/4318
