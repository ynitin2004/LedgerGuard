# Tasks & Milestones

Owner: you. One milestone at a time; the next starts only after acceptance and approval.
Status: ✅ done · 🟡 in progress / partially verified · ⬜ not started

| M | Weeks | Deliverable | Status |
|---|---|---|---|
| 0 | 1 | Foundation | ✅ done — verified locally and CI green (run 42002451764) |
| 1 | 2 | api_clients, transactions API, idempotency, audit, problem+json | ✅ done — verified locally and CI green (run 42166619870) |
| 2 | 3–4 | Synthetic data, rules v1, features, LR model, policy, decisions endpoint, first k6 | ✅ done — verified locally and CI green (run 43839427935) |
| 3 | 5 | Outbox, publisher, consumers + inbox, retry/DLQ, replay CLI | ✅ done — verified locally, Linux suite, chaos drills, CI green (run 43857220697) |
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

## M2 — Risk decisioning
Acceptance (plan v2): the replay test reproduces decisions; MODEL_CARD has a measured PR-AUC; k6 locally at 200 RPS with p99 < 100 ms, recorded in BENCHMARK.md.

- [x] Synthetic generator (`ledgerguard/synthetic.py`) with three fraud patterns and legitimate noise; `scripts/seed_history.py`
- [x] Features (`risk/features.py`) shared by serving, training and replay; tenant-scoped history index
- [x] Redis velocity windows with Postgres fallback and rebuild (ADR-023)
- [x] Rules engine + `rules/v1.0.yaml` (7 rules), policy + `policy/v1.0.yaml`, engine with composite `policy_version` (ADR-020)
- [x] LR model v1 trained on synthetic data (JSON artifact, pure-Python scoring); gradient boosting measured and not adopted; `model_registry` + `scripts/register_model.py`
- [x] Migration `0003`: `risk_decisions` (append-only), `model_registry`, index swap (expand → contract), grants
- [x] Decision written with the transaction in one DB transaction; `GET /v1/transactions/{id}/decisions`; `503` without a model; readiness requires the engine
- [x] Rate limiting, fail-open (ADR-025); API-key lookup cache (ADR-024); bounded DB timeouts
- [x] Replay: `scripts/verify_decisions.py` (143,464 / 143,464 matched) + integration and property tests
- [x] MODEL_CARD with measured PR-AUC 0.5358 (policy: decline precision 0.72 / recall 0.41)
- [x] k6 (`grafana/k6:1.7.0`, ADR-010) at 200 RPS: p99 44 / 42 / 59 ms over 3 runs, 0 errors (BENCHMARK.md, ADR-022)
- [x] Tests: 111 passing locally (+1 ML test that runs in CI); CI registers the model, checks a decision and replays
- [x] CI green: https://github.com/ynitin2004/LedgerGuard/actions/runs/43839427935

## M3 — Event backbone
Acceptance (plan v2): kill the publisher mid-batch under load, then no lost events and duplicates deduped (a test asserts counts); consumer lag metric visible.

- [x] Migration `0004`: `outbox_events` (partial unpublished index), `consumer_inbox`, grants
- [x] Hot path writes `transaction.evaluated` v1 to the outbox in the payment's transaction
- [x] Versioned event schemas + envelope; retry/DLQ routing with full-jitter backoff (pure, unit-tested)
- [x] Outbox publisher (SKIP LOCKED, idempotent producer, parallel-safe) as worker role + container
- [x] Consumer runner: inbox dedup, **batch inbox** (one transaction per batch), retry topic with per-consumer targeting, DLQ, replay CLI, 10 s failover
- [x] Feature-updater consumer (reliable Redis windows, ADR-031)
- [x] Metrics: `consumer_lag`, `outbox_backlog`, `outbox_oldest_age_seconds`; `reconcile` command
- [x] Acceptance test on real Redpanda (kill mid-batch under load; counts asserted) + DLQ/replay test; `test-linux` runner (ADR-033)
- [x] Chaos drill on the real stack (publisher + consumer SIGKILL under 200 RPS): reconciled exactly; drain 754 events/s (1 replica) → 1,419 (3 replicas)
- [x] Hot-path benchmark with outbox: p50 12.8–13.0 / p93 22.1–22.3 / p97 30.8–32.8 / p99 51.8–56.1 ms at 200 RPS
- [x] CI stack job runs both workers and reconciles
- [x] CI green: https://github.com/ynitin2004/LedgerGuard/actions/runs/43857220697

## M6 — scaling additions (user request 2026-11-16; proposals ADR-026..030)
- [ ] PgBouncer (transaction pooling) + replica-count load test that shows the connection wall and the fix (ADR-026)
- [ ] Monthly time partitioning for append-mostly tables + 10× data-volume benchmark (ADR-027)
- [ ] Read replica routing for read-heavy endpoints + mixed-load benchmark (ADR-028)
- [ ] Autoscaling signals (CPU/p99 for API, consumer lag + outbox age for workers) + scale-out drill; partition-count rationale (ADR-029)
- [ ] Record measured single-primary limits and the sharding trigger and key; multi-region out of scope (ADR-030)
- [ ] Every benchmark reports p50 / p93 / p97 / p99 (standing rule)

## Backlog items found during M0 planning (assigned)
- [x] M1: create `ledgerguard_app` DB role (least privilege; needed for `REVOKE UPDATE, DELETE` on append-only tables)
- [x] M1: idempotency janitor (delete expired rows) — runs as `apps/worker janitor`
- [x] M2: API rate limiting (plan A8: Redis-backed, fail-open); the alert comes with M6 (ADR-025)
- [x] M2: API-key lookup cache, in-process (ADR-024). Redis cache of completed idempotent responses: **not needed**. Replays already cost one indexed read; revisit only if a benchmark shows replays matter
- [x] M3: confluent-kafka is blocked on the primary dev PC; workers run in the app image (compose profile `workers`) and Kafka tests in `test-linux` (ADR-022/033)
- [ ] M6: stage spans and `decisions_total` / `decision_stage_duration_ms` metrics (moved from M2, OBSERVABILITY.md); fault-injection test for stalled DB (F4d)
- [x] M2: pin the `grafana/k6` image tag: `1.7.0` (ADR-010)
- [ ] M6: trace backend (Tempo/Jaeger) to replace the collector's debug exporter
- [ ] M6: dependency audit in CI (pip-audit or Dependabot)
- [ ] M10: `compose.prod.yml`, `infra/caddy`, `infra/ec2-bootstrap.sh` (intentionally not created in M0)
