# LedgerGuard AI

A simulated payment-risk intelligence platform. Each payment is decided by a deterministic, versioned pipeline (rules + ML model + explicit policy) and written in one atomic Postgres transaction with a transactional outbox. Idempotent consumers process the events. An AI investigator, which is **advisory only**, writes evidence-grounded case summaries for analysts.

> **Status: Milestone 3 (event backbone)**: every decision also emits an event through a transactional outbox to Redpanda; consumers process each event exactly once, even when publishers and consumers are killed under load. Previously, **Milestone 2 (risk decisioning)**: Every payment is decided in the same database transaction by versioned rules + a logistic-regression model + an explicit policy, and every decision can be replayed exactly. Builds on M0/M1: API-key auth, concurrency-safe idempotency, append-only audit trail, RFC 9457 errors. All data is synthetic. See [docs/TASKS.md](docs/TASKS.md). All data is synthetic.

## What this project demonstrates (target, by milestone)
| Capability | Milestone |
|---|---|
| Concurrency-safe idempotency (PG unique key, no app locks): **50 concurrent same-key requests → 1 row**, [test](tests/concurrency/test_idempotency_race.py) | M1 ✅ |
| Reproducible, versioned risk decisions (rules + LR model + policy): **143,464 of 143,464 replayed exactly** ([script](scripts/verify_decisions.py)); PR-AUC 0.536 on held-out synthetic data ([model card](docs/MODEL_CARD.md)) | M2 ✅ |
| Hot path at **200 RPS, p99 42–59 ms** over 3 runs ([k6 script](load/transactions.js), [results](docs/BENCHMARK.md)) | M2 ✅ |
| Transactional outbox → Redpanda → inbox-deduplicated consumers, retry/DLQ/replay: **publisher and consumer SIGKILLed under 200 RPS, 36,002 events reconciled exactly**; consumer 754 → 1,419 events/s from 1 → 3 replicas ([results](docs/BENCHMARK.md)) | M3 ✅ |
| LLM investigations with structured output and evidence-ID validation | M4 |
| Analyst auth (Supabase JWT), signed webhooks with backoff | M5 |
| Failure matrix tests, k6 load results, Grafana dashboards → `v0.1.0` | M6 |
| Measured RAG (tsvector baseline → pgvector hybrid + rerank, CI eval gate) | M7–8 |

## Quick start (Windows, PowerShell)
Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), git. uv uses Python 3.12 (`uv python install 3.12` if you don't have it).
```powershell
git clone <repo> ledgerguard; cd ledgerguard
uv sync
./scripts/dev.ps1 up        # starts the stack; copies .env.example -> .env if missing
./scripts/dev.ps1 migrate
./scripts/dev.ps1 register-model   # activates the risk model (models/lr-v1.json)
./scripts/dev.ps1 test
./scripts/dev.ps1 api       # http://127.0.0.1:8000/docs
./scripts/dev.ps1 api-client -Name demo   # prints an API key once
```
Linux/macOS: the same targets via `make up`, `make migrate`, `make test`, `make api`, `make api-client NAME=demo`.

Try it (Git Bash / Linux; replace `$KEY`):
```bash
curl -s -X POST http://127.0.0.1:8000/v1/transactions -H "X-API-Key: $KEY" -H "Idempotency-Key: order-1" \
  -H "content-type: application/json" \
  -d '{"merchant_id":"m_1","merchant_category":"5411","customer_token":"cus_1","amount_minor":1250,"currency":"usd"}'
```
Send it twice: the second response is identical and carries `Idempotent-Replayed: true`. Change the amount but keep the key, and you get `409`.

| URL | What |
|---|---|
| http://127.0.0.1:8000/healthz | Liveness (process up) |
| http://127.0.0.1:8000/readyz | Readiness (Postgres + schema head required; Redis/Redpanda reported) |
| http://127.0.0.1:8080 | Redpanda Console |
| http://127.0.0.1:9090 | Prometheus |
| http://127.0.0.1:3000 | Grafana (credentials from `.env`) |

## Repository layout
```
apps/api/                 FastAPI app factory, routers, dependency providers
apps/worker/              worker entrypoint: python -m apps.worker janitor (M1); more roles M3+
packages/ledgerguard/     core package (src layout): config, observability, infrastructure, ...
migrations/               Alembic (async env), versions/
rules/ policy/ models/    versioned decision assets: rules v1.0, thresholds v1.0, model v1 (JSON)
tests/                    unit/ (incl. Hypothesis), integration/, concurrency/ (Testcontainers)
load/                     k6 script + committed benchmark summaries
infra/                    postgres init (app role), otel-collector, prometheus, grafana provisioning
docs/                     PRD, architecture, data model, API, ADRs, testing, security, ...
scripts/                  dev.ps1 (Windows task runner, mirrors Makefile), create_api_client.py
```

## Documentation
[PRD](docs/PRD.md) · [Architecture](docs/ARCHITECTURE.md) · [Data model](docs/DATA_MODEL.md) · [API](docs/API_SPEC.md) · [Decisions](docs/DECISIONS.md) · [Testing](docs/TESTING.md) · [Security](docs/SECURITY.md) · [Observability](docs/OBSERVABILITY.md) · [Failure matrix](docs/FAILURE_MATRIX.md) · [Benchmarks](docs/BENCHMARK.md) · [Model card](docs/MODEL_CARD.md) · [Tasks](docs/TASKS.md)

Every performance or model claim in this README links to the script that produced it.
