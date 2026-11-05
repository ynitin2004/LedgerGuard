# Tasks & Milestones

Owner: you. One milestone at a time; the next starts only after acceptance and approval.
Status: ✅ done · 🟡 in progress / partially verified · ⬜ not started

| M | Weeks | Deliverable | Status |
|---|---|---|---|
| 0 | 1 | Foundation | 🟡 implemented and verified locally; CI not yet run on GitHub |
| 1 | 2 | api_clients, transactions API, idempotency, audit, problem+json | ⬜ |
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
- [ ] First commit + push to a GitHub remote, then a green CI run (needs your remote / approval)

## Backlog items found during M0 planning (assigned)
- [ ] M1: create `ledgerguard_app` DB role (least privilege; needed for `REVOKE UPDATE, DELETE` on append-only tables)
- [ ] M1: idempotency janitor (delete expired rows) — runs as `apps/worker janitor`
- [ ] M1/M2: API rate limiting (plan A8 mentions fail-open behaviour but assigns no milestone)
- [ ] M2: choose and pin the `grafana/k6` image tag (ADR-010)
- [ ] M6: trace backend (Tempo/Jaeger) to replace the collector's debug exporter
- [ ] M6: dependency audit in CI (pip-audit or Dependabot)
- [ ] M10: `compose.prod.yml`, `infra/caddy`, `infra/ec2-bootstrap.sh` (intentionally not created in M0)
