# Architecture Decision Records

Format: context → decision → consequences. Status is **Accepted** unless noted.
Source of truth for scope: *LedgerGuard AI — Final Project Plan v2* (supersedes v1).

| ADR | Title | Milestone |
|---|---|---|
| 001 | Modular monolith + worker roles | M0 |
| 002 | Redpanda instead of Apache Kafka | M0 |
| 003 | Pin Python 3.12 | M0 |
| 004 | Postgres 16 locally, Supabase hosted | M0 |
| 005 | Logistic-regression baseline model | M2 (decided) |
| 006 | pgvector gated behind a measured retrieval baseline | M7 (decided) |
| 007 | No Temporal / durable-workflow engine | M0 |
| 008 | asyncpg as the PostgreSQL driver | M0 |
| 009 | Use 127.0.0.1, never `localhost`, for host-side URLs | M0 |
| 010 | k6 runs from Docker, not a host install | M0 |
| 011 | Liveness vs readiness semantics | M0 |
| 012 | Metrics path: OTLP → Collector → Prometheus | M0 |
| 013 | Makefile + PowerShell task runner | M0 |
| 014 | Supabase Auth JWT roles for analysts | M5 (decided) |
| 015 | Package layout and app factory | M0 |
| 016 | Repository location | M0 |

---

## ADR-001 Modular monolith + worker roles
**Context.** The system needs a low-latency hot path plus several asynchronous consumers. Microservices would add network hops, deployment surface and distributed-transaction problems without benefit at portfolio scale.
**Decision.** One importable core package (`packages/ledgerguard`), one API process (`apps/api`), one worker entrypoint whose role is chosen on the command line (`apps/worker`). Boundaries are enforced by package structure: `domain` has no I/O imports; `application` depends on repository protocols; routers call use cases only.
**Consequences.** Simple deploys (one image, many roles); scaling is per role; module boundaries must be kept by review and import discipline rather than by the network.

## ADR-002 Redpanda instead of Apache Kafka
**Context.** The outbox needs a Kafka-API broker locally and in CI.
**Decision.** Redpanda: a single binary, no ZooKeeper or KRaft tuning, starts in seconds, about 1 GB RAM in dev mode, and ships Console. Client: `confluent-kafka` with `enable.idempotence=true`, `acks=all` (M3).
**Consequences.** 100 % Kafka-API compatible, so code and skills transfer. Pick Kafka or MSK instead when managed operations, Kafka Connect connectors or organisational standards require it.

## ADR-003 Pin Python 3.12
**Context.** The host has 3.14 as its system Python; wheels for scikit-learn, confluent-kafka and testcontainers dependencies are most reliable on 3.12.
**Decision.** `.python-version` = 3.12 and `requires-python = ">=3.12,<3.13"`. uv uses an existing user-level 3.12 interpreter (`uv python install 3.12` if absent). The system Python is never modified.
**Consequences.** Revisit when 3.13 and 3.14 wheels are universal for the stack.

## ADR-004 Postgres 16 locally, Supabase hosted
**Decision.** Local and CI use `pgvector/pgvector:0.8.0-pg16`, so Phase 2 needs no image swap. Hosted (M10) uses Supabase Postgres + Auth. Supabase RLS, Storage and Edge Functions are not used in the MVP.
**Consequences.** Migrations must stay plain PostgreSQL (no Supabase-only features) so the same Alembic history applies everywhere.

## ADR-005 Logistic-regression baseline model
Gradient boosting is adopted only if it improves PR-AUC by ≥ 0.05 on the held-out synthetic set. Metrics are recorded in MODEL_CARD.md.

## ADR-006 pgvector gated behind a measured retrieval baseline
The `vector` extension is **not** enabled until M7 measures a Postgres `tsvector` baseline (recall@6 on a 60-question golden set). M8 adopts pgvector only if the data justifies it. The M0 baseline migration therefore enables no extensions.

## ADR-007 No Temporal / durable-workflow engine
The outbox, idempotent consumers, retry topics and a DLQ cover the MVP's reliability needs with infrastructure already present. Revisit if multi-step human-in-the-loop workflows exceed what case state machines handle.

## ADR-008 asyncpg as the PostgreSQL driver
**Context.** psycopg 3's async mode cannot run on Windows' default `ProactorEventLoop`, which is what uvicorn and `asyncio.run` use on the primary dev machine.
**Decision.** SQLAlchemy 2 async engine with `asyncpg` for the app, Alembic (async `env.py`) and tests.
**Consequences.** One driver everywhere. `postgresql+asyncpg://` URLs. Sync scripts (M2 training) use the same engine via `asyncio.run`.

## ADR-009 Use 127.0.0.1, never `localhost`, for host-side URLs
**Context.** Measured on the dev machine (2026-11-05): an asyncpg connect to `localhost:55432` took **2.04 s**, while `127.0.0.1:55432` took **0.01 s**. Windows resolves `localhost` to `::1` first, compose publishes on IPv4 only, and the IPv6 attempt times out before falling back. `/readyz` timed out at 2 s because of this.
**Decision.** All host-side URLs in `.env.example`, settings defaults and the Redpanda external advertised address use `127.0.0.1`.
**Consequences.** New connections cost the expected time; the hot-path p99 target is not distorted by name resolution.

## ADR-010 k6 runs from Docker
The plan listed `winget install k6` in M0. k6 is first used in M2 and the execution rules require approval for system installs. Instead, load tests run with `docker run grafana/k6` (pinned tag chosen in M2). Nothing is installed on the host.

## ADR-011 Liveness vs readiness semantics
- `/healthz` (liveness): the process and its event loop respond. It touches no dependencies, so a database outage never causes restart loops.
- `/readyz` (readiness): 503 if **Postgres** is unreachable or its Alembic revision differs from the code's head. Redis and Redpanda are reported but never fail readiness: plan A8 degrades around Redis (Postgres fallback), and A9 makes broker outages invisible to the hot path (outbox). Removing instances from the load balancer for a degradable dependency would turn a partial outage into a full one.
- Each probe has a timeout (`LG_READINESS_TIMEOUT_S`, default 2 s), and probes run concurrently.

## ADR-012 Metrics path: OTLP → Collector → Prometheus
The app exports traces and metrics over OTLP/HTTP to the OpenTelemetry Collector. The collector exposes app metrics on `:8889` for Prometheus, and Grafana reads Prometheus. The app has no `/metrics` endpoint, which keeps one path and allows vendor-neutral backends later. Probe endpoints are excluded from instrumentation. Traces use the collector's debug exporter until a trace backend is chosen in M6.

## ADR-013 Makefile + PowerShell task runner
`make` is not installed on the Windows dev machine. The `Makefile` serves CI, Linux and EC2. `scripts/dev.ps1` mirrors every target on Windows. Both stay in sync in the same PR.

## ADR-014 Supabase Auth JWT roles for analysts
The analyst role lives in `app_metadata.role` ∈ {analyst, admin}. The verifier is pluggable so local dev can mint JWTs with a dev secret (`scripts/mint_dev_jwt.py`, M5).

## ADR-015 Package layout and app factory
`packages/ledgerguard` uses a `src/` layout (`packages/ledgerguard/src/ledgerguard/...`), so tests import the installed package, not the working tree by accident. The root project is a non-packaged uv workspace root holding `apps/` (run from the repo root). The API is built by `create_app()` and run with `uvicorn --factory`. No module-level app exists, so importing never reads settings or opens connections.

## Notes on pinned images
`redpandadata/console` is pinned to **v2.8.0**. A truncated v2.7.2 pull on the dev machine left corrupted layers, and v2.8.0 was verified intact. All images are pinned to exact tags in `docker-compose.yml`.

## ADR-016 Repository location
**Context.** Plan v2 put the repo at `C:\dev\ledgerguard` (outside OneDrive). The user's VS Code workspace is `C:\Users\SSIPLIT1538\Downloads\guard`, and they asked for the project to live there.
**Decision.** The repository root is `C:\Users\SSIPLIT1538\Downloads\guard`. That folder is not synced by OneDrive, so the plan's reason (R2: OneDrive + Docker bind mounts) still holds.
**Consequences.** Nothing in the code or config depends on the path. The compose project name stays `ledgerguard`, so existing data volumes are reused.
**Finding.** Windows Application Control blocks some console-script shims (e.g. `.venv\Scripts\alembic.exe`, os error 4551) in this folder. All task runners, CI and docs therefore invoke tools as `uv run python -m <tool>`, which works on every platform.
