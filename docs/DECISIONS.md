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

## ADR-017 Two database roles: schema owner and least-privilege runtime role
**Context.** Plan v2 requires `audit_events` to be append-only for the application (`REVOKE UPDATE, DELETE`). A REVOKE means nothing while the API connects as the table owner.
**Decision.** Migrations and admin scripts connect as the owner (`LG_MIGRATION_DATABASE_URL`). The API and workers connect as `ledgerguard_app` (`LG_DATABASE_URL`). Each migration grants exactly the privileges its code needs; there are no default privileges, so a new table is invisible to the app until a reviewed GRANT says otherwise. The migration creates the role `NOLOGIN` if it is missing. The login password is environment configuration (local: a Postgres init script; hosted: set once by the operator). Roles are cluster-wide, so downgrades revoke privileges but keep the role.
**Consequences.** The audit trail, API-client table and schema are protected from app bugs and from a compromised app process, and tests prove it. One more connection string to manage. Existing local volumes need `db-role` once.

## ADR-018 API error and idempotency details
- Errors are RFC 9457 problem+json. Problem `type`s are URNs (`urn:ledgerguard:problem:<name>`): stable identifiers that don't imply a documentation URL that doesn't exist.
- Missing/invalid `Idempotency-Key` is `422` like any other validation error, not `400`, so clients handle one validation shape.
- Missing, unknown and revoked API keys share one `401`, so the response doesn't reveal which keys exist. Another client's transaction is `404`, not `403`.
- The claim is a single `INSERT … ON CONFLICT DO UPDATE … WHERE expires_at <= now()`. It covers new keys, expired keys (taken over) and live keys (waits on the PK, then re-reads) without raising, so the transaction stays usable and no app lock or retry loop is needed.
- Replays carry `Idempotent-Replayed: true` and the stored `jsonb` body. It is the same JSON document, though key order may differ.
- Rate limiting (plan A8) is deferred to M2 together with Redis on the hot path.

## ADR-019 Tooling pins forced by the primary dev PC
- **Hypothesis `<6.156`.** Since 6.156.1 (2026-07-03) Hypothesis imports a compiled `_native` extension. Windows Application Control on the primary dev PC blocks it ("DLL load failed … Application Control policy"), the same policy as ADR-016. 6.155.x is pure Python and fully featured for our use. Revisit when this PC is no longer used.
- **`tzdata` (dev).** Alembic's `timezone = UTC` needs the IANA database, which Windows doesn't ship; `tzdata` is the official data-only package.

## ADR-020 Rules as data, model as JSON, everything versioned
**Context.** Plan s.7 needs reproducible decisions: the same snapshot + versions must give the same answer forever.
**Decision.**
- Rules are YAML data (`rules/v<version>.yaml`): `feature <op> value` conditions, all of which must hold. There is no expression language and no `eval`, so a rules file cannot run code. Policy thresholds live in `policy/v<version>.yaml`. The file name must equal the declared version, and released files are pinned by SHA-256 in a test, so editing a released version fails CI.
- The model is exported as JSON (standardisation, coefficients, intercept, metrics) and stored whole in `model_registry.artifact` with its sha256. The API scores it in pure Python. There is no pickle (no code execution on load) and no numpy at runtime.
- Scores are rounded to 5 decimals *before* the policy compares them, which is the precision stored in `numeric(6,5)`. Stored values therefore re-derive the same decision exactly.
**Consequences.** `scripts/verify_decisions.py` can prove that every stored decision reproduces (143,464 of 143,464 did). Changing anything means a new version file or model version.

## ADR-021 Model training runs where numpy can load
**Context.** On the primary dev PC, Windows Application Control blocks numpy's compiled core ("DLL load failed … Application Control policy"), so scikit-learn cannot run there. Same class of block as ADR-016/019.
**Decision.** Training needs numpy only offline. `make train` runs natively on Linux/macOS/CI; `./scripts/dev.ps1 train` runs the same script in a pinned Linux image (`infra/ml/Dockerfile`: uv 0.11.29 on python:3.12.10-slim-bookworm). numpy and scikit-learn are a separate `ml` dependency group. It is a default group so mypy sees numpy's types, but the API never imports it. The scikit-learn parity and reproducibility test skips where numpy cannot load and runs in CI.
**Consequences.** No production dependency on numerical libraries. Training is reproducible from any machine with Docker.

## ADR-022 Benchmark the API in a Linux container
**Context.** Plan acceptance for M2 is 200 RPS with p99 < 100 ms. One uvicorn process tops out near 100 RPS (about 10 ms of Python per request), so several workers are needed. On Windows, uvicorn workers sometimes fail `listen()` on the shared socket (`OSError [WinError 10022]`), and orphaned workers keep accepting connections after their parent dies. In one run that made 25 requests hang.
**Decision.** Benchmarks run the API from `infra/api/Dockerfile` (uvicorn, 4 workers) in compose profile `bench`, with k6 on the same Docker network (`make bench-up`, `make load`). Production is Linux (M10), so this is also the representative setup. The image is a dev/bench image; M10 hardens it.
**Consequences.** Benchmark numbers exclude Docker Desktop port forwarding. The containerised API also runs Linux-only wheels (confluent-kafka is now blocked on the dev PC too), which M3 workers can reuse.

## ADR-023 Redis velocity windows with a Postgres fallback (plan A8)
**Context.** The plan keeps velocity features in Redis with Postgres as the fallback. The user confirmed this in M2 over a Postgres-only alternative.
**Decision.**
- One sorted set per (client, customer): member = transaction id, score = exact integer epoch microseconds. Window bounds are computed in Python; the Lua script does no arithmetic, because its 14-digit number formatting would round timestamps. Windows are half-open, `now - w < t <= now`, exactly like `velocity_from_history`, and `now` is the transaction's database timestamp.
- A window is trusted only if it contains a `!ready` marker. A miss computes velocity from the Postgres history (which the other features read anyway) and rebuilds the window with ZADD, a union. Each commit ZADDs the payment after commit. Because both are unions, a rebuild racing a commit never loses the payment.
- If a post-commit write fails, the window is deleted so the next read rebuilds it. If Redis is unreachable, reads fall back to Postgres. The Redis client has a 50 ms timeout and **no retries**: redis-py 8 defaults to 10 retries with backoff, which would turn an outage into slow requests.
**Consequences.** With Redis down, features are identical and tested (`test_redis_down_gives_the_same_features_as_redis_up`), just slower. Known gap: if a write fails *and* the following delete also fails while reads still succeed, a window under-counts until its TTL (65 min). M3's outbox-driven feature-updater makes the writes at-least-once.

## ADR-024 In-process cache for API-key lookups
**Context.** Authenticating every request cost a separate database transaction (4 round trips), the largest single item in the hot path.
**Decision.** Successful lookups are cached per process for `LG_API_KEY_CACHE_TTL_S` (default 10 s). The cache is keyed by the key's SHA-256 (never the raw key) and LRU-bounded at 10,000 entries. Misses are never cached, so random invalid keys cannot fill it. Set the TTL to 0 to disable.
**Consequences.** Revoking a key takes up to 10 s to apply in each API process (documented in SECURITY.md). Together with `now()` returned by the idempotency claim, this removed about 5 round trips per request; p99 at 200 RPS went from 49–156 ms to 42–59 ms.

## ADR-025 Rate limiting: fixed one-second window per client, fail-open
Moved from the M1 backlog. `INCR lg:rl:{client}:{epoch_second}` + `EXPIRE 2` in one pipeline. Above `LG_RATE_LIMIT_PER_SECOND` (default 1000; 0 disables) the API returns `429` problem+json with `Retry-After: 1`. Redis errors allow the request (plan A8: fail-open), with a warning log; M6 adds the alert. A fixed window allows up to 2× the limit across a second boundary, which is acceptable for abuse protection; it is not billing-grade metering.

## Proposed for M6: scaling Postgres and the platform (user request 2026-11-16)
These are **proposals**. Each is decided in M6 with a load test that shows the limit first, then the fix (BENCHMARK.md). The API and workers already scale horizontally because they hold no state; Postgres is the shared resource that eventually limits the system.

### ADR-026 (Proposed) Connection pooling with PgBouncer
**Problem.** Each API worker opens up to `db_pool_size + db_max_overflow` = 20 connections. 5 instances × 4 workers could open 400, but Postgres defaults to `max_connections = 100`, and every idle connection costs server memory. This is the first wall when adding replicas.
**Proposal.** PgBouncer in transaction-pooling mode between the services and Postgres; app pools shrink to a few connections per worker. Check asyncpg prepared-statement caching against transaction pooling (`statement_cache_size=0` or PgBouncer ≥ 1.21 prepared-statement support).
**Prove it.** A k6 run with many API replicas that exhausts connections without PgBouncer and holds p99 with it.

### ADR-027 (Proposed) Time-partition `transactions`, `risk_decisions`, `audit_events`, `outbox_events`
**Problem.** These tables only grow. Index depth and vacuum cost rise with size, and deleting old rows is expensive.
**Proposal.** Native declarative partitioning by month on `created_at` (pg_partman or a migration helper creates future partitions). Hot-path queries already filter on recent time windows, so partition pruning applies. Retention becomes `DETACH`/`DROP PARTITION` instead of `DELETE`. Primary keys must include `created_at`, so FK targets need a design review.
**Prove it.** Insert and history-query latency at 10× the current data volume, with and without partitions.

### ADR-028 (Proposed) Read replicas for read-heavy endpoints
**Problem.** Analyst queues (M5), decision history and dashboards compete with the write path on the primary.
**Proposal.** Route `GET` analyst/decision reads to a replica through a second engine (`LG_DATABASE_READ_URL`). The hot path and anything that must see its own writes stays on the primary. Document the replication-lag semantics per endpoint.
**Prove it.** Mixed read/write k6 profile: primary CPU and write p99 with and without the replica.

### ADR-029 (Proposed) Autoscaling signals and Kafka partition strategy
**Problem.** Scaling is manual (ECS is documented only, M10). Consumer parallelism is capped by the partition count of `txn.events`.
**Proposal.** Scale the API on CPU/p99 and consumers on **consumer lag** (M3 metric) and outbox backlog age. Choose the partition count for the target consumer parallelism (start: 6) and document re-partitioning cost: keys move, so ordering per key holds only within a partition generation.
**Prove it.** A lag-driven scale-out drill: double the load, add consumers, lag returns to its baseline within N minutes.

### ADR-030 (Proposed) Out of scope: multi-region and sharding
**Decision to record.** One region, one primary. State the limits measured in M6, what would trigger sharding (e.g. sustained write load beyond one primary's measured ceiling), and the likely shard key (`client_id`, since every query is tenant-scoped).

## ADR-031 Velocity windows: API fast-path write + feature-updater as the reliable writer
**Context.** M2 records each payment in its Redis window right after commit (best effort). The plan's M3 moves feature updates to a consumer of `txn.events`. If the consumer were the *only* writer, windows would lag behind by the consumer lag, and a burst inside that lag would be under-counted.
**Decision.** Keep both. The API records immediately after commit (fresh windows). The `feature-updater` consumes `transaction.evaluated` and records again (at-least-once, survives API crashes and Redis blips). `ZADD` of the same member is idempotent, so the double write cannot double-count.
**Consequences.** Closes the ADR-023 gap ("write failed and invalidate failed → under-count until TTL"): the event repairs the window. Costs one extra Redis write per payment in the background.

## ADR-032 Event pipeline design (outbox, publisher, consumers, retry, DLQ)
- **Outbox** row in the payment's transaction. **Publisher:** `SELECT … FOR UPDATE SKIP LOCKED LIMIT 100` → produce (idempotent producer, `acks=all`) → wait for acks → mark `published_at` → commit. A crash between ack and commit re-sends (duplicates by design). Any number of publishers can run in parallel.
- **Topic** `txn.events`: 6 partitions, key = transaction id (plan s.4). `txn.events.retry` (6) and `txn.events.dlq` (1).
- **Consumers:** manual offset commits only after processing. Each consumer runs **two groups**, `<name>` (main topic) and `<name>.retry` (retry topic), so waiting out one retry's backoff never blocks fresh events. Retry messages carry `target_consumer`, `attempt`, `retry_at` and `error` headers, so several consumers can share one retry topic.
- **Failure routing:** poison (bad headers, unknown schema/version, invalid body) → DLQ immediately. Other errors → retry with full-jitter exponential backoff (1 s base, 60 s cap), at most 5 attempts → DLQ. **Replay** (`apps.worker replay-dlq`) reads the DLQ with a throwaway group and re-sends to the retry topic for the original consumer.
- **Batch inbox (throughput):** the main loop polls up to 200 messages and processes them in **one database transaction**: one multi-row `INSERT … ON CONFLICT DO NOTHING RETURNING` identifies new events, the handler runs for those, one commit, then one offset commit. On any error the batch rolls back and is re-handled one message at a time, isolating the bad one. Handlers must be idempotent. Measured: 754 events/s per replica, against about 80–160 per replica with per-message transactions and commits (BENCHMARK.md).
- **Failover:** `session.timeout.ms=10000` (default 45 s). A killed consumer's partitions move within about 10 s instead of stalling for about 30 s, as observed in the first chaos run.
- **Schemas:** `transaction.evaluated` v1 (Pydantic, `extra=forbid`). Versions are immutable; additive change ships as v2 next to v1.

## ADR-033 Run the complete test suite in Linux from the Windows dev PC
`make test-linux` / `./scripts/dev.ps1 test-linux` run pytest inside the pinned `infra/ml` image with the Docker socket mounted (`TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal`, Ryuk disabled). This is the only way to run the Kafka and scikit-learn tests on the primary PC (ADR-021/022). CI runs them natively on Linux.
