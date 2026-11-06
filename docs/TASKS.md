# Tasks & Milestones

Owner: you. One milestone at a time; the next starts only after acceptance and approval.
Status: ✅ done · 🟡 in progress / partially verified · ⬜ not started

| M | Weeks | Deliverable | Status |
|---|---|---|---|
| 0 | 1 | Foundation | ✅ done — verified locally and CI green (run 42002451764) |
| 1 | 2 | api_clients, transactions API, idempotency, audit, problem+json | ✅ done — verified locally and CI green (run 42166619870) |
| 2 | 3–4 | Synthetic data, rules v1, features, LR model, policy, decisions endpoint, first k6 | ⬜ |
| 3 | 5 | Outbox, publisher, consumers + inbox, retry/DLQ, replay CLI | ⬜ |
| 4 | 6–7 | Investigator: evidence, LLM client, validation, cases | ⬜ |
| 5 | 8 | Supabase JWT, investigations API, webhooks | ⬜ |
| 6 | 9–10 | Failure matrix, chaos, k6 suite, dashboards → **tag v0.1.0** | ⬜ |
| 7 | 11 | RAG prerequisite: corpus, tsvector baseline, golden set | ⬜ |
| 8 | 12–13 | Chunkers, embeddings, pgvector (if justified), hybrid + rerank, evals CI | ⬜ |
| 9 | 14–15 | Next.js analyst dashboard | ⬜ |
| 10 | 16 | EC2 + compose.prod + Caddy, Supabase prod, SSM, demo | ⬜ |

## M0 — Foundation
Acceptance: `make up && make test` green on a clean clone (`./scripts/dev.ps1 up; ./scripts/dev.ps1 test` on Windows).

- [x] Repo at `C:\Users\SSIPLIT1538\Downloads\guard` (your VS Code workspace; moved from the planned `C:\dev\ledgerguard`, ADR-016), git initialised, `.gitignore`, `.gitattributes`, `.env.example`
- [x] uv workspace, Python 3.12 pinned, `uv.lock`; runtime vs dev dependencies separated
- [x] Core: typed settings, structlog, correlation-ID middleware, OTel export, async engine, declarative base with naming convention
- [x] `/healthz` (liveness) and `/readyz` (Postgres + schema head required; Redis/Redpanda optional)
- [x] Alembic (async env, empty baseline `0001`, round-trip tested)
- [x] docker-compose: postgres/pgvector, redis, redpanda, console, otel-collector, prometheus, grafana with healthchecks, volumes, env config
- [x] Makefile + `scripts/dev.ps1`
- [x] Tests: 13 unit + 1 integration (Testcontainers)
- [x] Ruff, mypy (strict), pytest config; GitHub Actions `ci.yml` (quality + compose smoke jobs)
- [x] Docs: CLAUDE.md, README, PRD, ARCHITECTURE, DATA_MODEL, API_SPEC, TASKS, TESTING, DECISIONS, SECURITY, OBSERVABILITY, FAILURE_MATRIX, BENCHMARK, MODEL_CARD
- [x] Pushed to github.com/ynitin2004/LedgerGuard; CI green: https://github.com/ynitin2004/LedgerGuard/actions/runs/42002451764

## M1 — Transactions API
Acceptance (plan v2): Hypothesis property tests; Testcontainers test where 50 concurrent same-key requests create exactly 1 transaction row and return identical responses; a different body with the same key returns 409.

- [x] Migration `0002`: `api_clients`, `transactions`, `idempotency_records`, `audit_events` (reviewed autogenerate + hand-written grants); upgrade/downgrade round trip tested
- [x] `ledgerguard_app` least-privilege role; API/workers connect as it; audit trail append-only (ADR-017)
- [x] `domain/` (transaction invariants, canonical hashing, API keys) and `application/` (ports, use cases), SQLAlchemy repositories + Unit of Work
- [x] `X-API-Key` auth (SHA-256 at rest); `scripts/create_api_client.py`
- [x] `POST /v1/transactions` with `Idempotency-Key`: replay, 409 on conflict, 24 h TTL, expired key = new
- [x] `GET /v1/transactions/{id}` scoped to the calling client
- [x] Audit event in the same transaction as the business write
- [x] RFC 9457 problem+json for every error, with correlation ID
- [x] Janitor worker: `python -m apps.worker janitor`
- [x] Tests: 50 unit/property + 15 integration/concurrency (65 total, all passing locally)
- [x] CI stack job: create client → POST → replay → 409 against the compose stack
- [x] CI green: https://github.com/ynitin2004/LedgerGuard/actions/runs/42166619870

## Backlog items found during M0 planning (assigned)
- [x] M1: create `ledgerguard_app` DB role (least privilege; needed for `REVOKE UPDATE, DELETE` on append-only tables)
- [x] M1: idempotency janitor (delete expired rows) — runs as `apps/worker janitor`
- [ ] M2: API rate limiting (plan A8: Redis-backed, fail-open with alert); moved from M1 with Redis on the hot path (ADR-018)
- [ ] M2: Redis read-through cache for completed idempotent responses and API-key lookups (plan s.6; an optimisation, not correctness)
- [ ] M2: choose and pin the `grafana/k6` image tag (ADR-010)
- [ ] M6: trace backend (Tempo/Jaeger) to replace the collector's debug exporter
- [ ] M6: dependency audit in CI (pip-audit or Dependabot)
- [ ] M10: `compose.prod.yml`, `infra/caddy`, `infra/ec2-bootstrap.sh` (intentionally not created in M0)
