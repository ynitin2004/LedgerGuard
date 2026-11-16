# Testing Strategy

## Layers
| Suite | Location | Needs | Status |
|---|---|---|---|
| Unit | `tests/unit/` | nothing | **M0: health/readiness, correlation IDs. M1: validation, problem+json** |
| Integration | `tests/integration/` (marker `integration`) | Docker (Testcontainers) | **M0: migrations gate readiness. M1: transactions API as the app role** |
| Property | Hypothesis, in unit/ | nothing | **M1: canonical hashing, amount invariants. M2: feature invariants, replay from JSON snapshots** |
| ML | `tests/unit/test_model_training.py` | numpy (CI) | **M2: retraining reproduces the committed artifact and metrics; pure-Python scoring = scikit-learn** (skipped where numpy cannot load) |
| Concurrency | `tests/concurrency/` (marker `integration`) | Docker | **M1: 50 concurrent same-key → 1 row; 20 different bodies → 1×201 + 19×409** |
| Failure | `tests/failure/` | Docker | Planned (M3/M6: FAILURE_MATRIX rows) |
| Contract | `tests/contract/` | nothing | Planned (M3: event schema compatibility) |
| Evals | `tests/evals/` | corpus | Planned (M8: retrieval + faithfulness gate) |
| Load | `load/` (k6 via Docker) | stack | Planned (M2/M6) |

## Principles
- Test behaviour at boundaries, not implementation details. Inject dependencies through `create_app(settings, probes=...)` and constructor arguments, not monkeypatching.
- Integration tests use the **same Postgres image as compose** (`pgvector/pgvector:0.8.0-pg16`).
- Tests never read the developer's `.env`. `tests/helpers.py:make_settings()` builds isolated settings and points optional dependencies at closed ports.
- Integration tests connect the API as the least-privilege `ledgerguard_app` role, exactly as deployed; fixtures and assertions use the owner (`tests/pg.py`). Each test creates its own API client, so tests sharing a module-scoped container stay independent.
- Async scenarios run with `asyncio.run()` inside plain tests (no pytest-asyncio): every scenario owns its event loop, engine and app lifespan.
- No production credentials anywhere. The LLM is mocked in tests (M4).
- Report real results; a skipped or failing test is never reported as passing.

## Commands (verified 2026-11-06 on Windows 11, Docker Desktop)
```powershell
uv run python -m pytest                       # all (2026-11-16): 111 passed, 1 skipped (~36 s)
uv run python -m pytest -m "not integration"  # unit + property: 85 passed, 1 skipped (~8 s)
# The skip is the scikit-learn test: numpy cannot load on this PC (ADR-021); it runs in CI.
./scripts/dev.ps1 test              # same as `make test`
```

## M0 test inventory
- `tests/unit/test_health.py`: `/healthz` is 200 with every dependency down; `/readyz` ready, degraded (optional failing → 200), not_ready (required failing → 503), and probe timeout.
- `tests/unit/test_correlation.py`: valid IDs are echoed, missing IDs get a UUID, unsafe IDs (CRLF injection, spaces, >128 chars, markup) are replaced.
- `tests/integration/test_db_readiness.py`: a fresh DB → 503 "migrations not applied"; `upgrade head` → 200 with `revision=0001`; `downgrade base` → 503; re-upgrade → 200.

## M1 test inventory
- `tests/unit/test_idempotency_hash.py` (Hypothesis): key order never changes the hash; canonical JSON round-trips; currency case and field order do not change the request hash; any amount change does; digests are SHA-256 hex.
- `tests/unit/test_transaction_validation.py`: floats, numeric strings and booleans rejected as `amount_minor`; range 1..2^63−1 (property-tested in the domain); currency, MCC and token formats; unknown fields rejected; API key generation and hashing.
- `tests/unit/test_problems.py`: 404/405/500 are problem+json with the correlation ID (exactly once, including on unhandled 500s); no exception text leaks; 401 without touching the DB; OpenAPI documents the error responses.
- `tests/integration/test_transactions_api.py`: create + get; audit row and stored response written in the same transaction; replay (field order, currency case); 409; per-client key scoping and cross-client 404; 401 for missing, unknown and revoked keys; 422 for missing or invalid Idempotency-Key; expired key treated as new + janitor purge; app role cannot UPDATE/DELETE/TRUNCATE audit events, INSERT API clients or run DDL.
- `tests/concurrency/test_idempotency_race.py`: 50 concurrent identical requests → 50×201 with identical bodies, 49 `Idempotent-Replayed`, 1 transaction row, 1 audit event; 20 concurrent different bodies on one key → 1×201 + 19×409, 1 row.
- `tests/integration/test_db_readiness.py` now round-trips `0002` too (downgrade to base, upgrade head).

## M3 test inventory
- `tests/unit/test_events.py`: envelope round trip through a Kafka message; malformed headers, unknown schema, invalid or extra fields are poison; canonical JSON; poison → DLQ; retries up to 5 then DLQ; property: backoff is bounded full jitter.
- `tests/integration/test_outbox.py` (Postgres, in-memory broker, **runs on Windows**): the event commits with the payment and replays add none; 4 parallel publishers send each event exactly once (SKIP LOCKED); a publisher killed after send re-sends 10 of 50 events and the inbox turns 60 deliveries into 50 effects; failed deliveries stay unpublished with the error; handler failure rolls back the inbox row; batch processing dedupes within and across batches and rolls back on failure.
- `tests/integration/test_kafka_pipeline.py` (real Redpanda + Postgres + Redis, **Linux only**): the M3 acceptance test (payments under concurrent load, publisher killed mid-batch, restarted: topic has > N messages, inbox exactly N, Redis has every payment, consumer lag metric present and back to 0); poison and exhausted retries reach the DLQ, and `replay-dlq` recovers the event.
- Chaos and throughput drills on the real compose stack: BENCHMARK.md (M3).

## M2 test inventory
- `tests/unit/test_risk_features.py`: half-open velocity windows, cold start, z-score rules (5 same-currency payments, cap), merchant/category/failure features; properties: counts consistent, features finite and rounded.
- `tests/unit/test_risk_engine.py`: released rules/policy files are byte-pinned; rules drive decisions (velocity → review, repeated failures / high amount → decline, history guards); policy thresholds and clamping; invalid rule sets rejected; file name must match version; model sha256 changes with any coefficient; stable sigmoid; property: a snapshot round-tripped through JSON reproduces the outcome.
- `tests/unit/test_synthetic.py`: same seed, same data; labels and patterns consistent; features use strictly earlier history.
- `tests/unit/test_auth_cache.py`: hits cached for the TTL, misses never cached, LRU bound, TTL 0 disables.
- `tests/integration/test_risk_api.py` (Postgres + Redis containers): a burst reaches review with `VELOCITY_5M` and evidence refs; decisions endpoint; Redis window marked ready; Redis down gives identical snapshots; a lost window is rebuilt; tenant-scoped history; every stored decision replays exactly; rate limit 429 and fail-open; no active model → 503 without writes, then ready after registration without a restart.
- `tests/integration/test_db_readiness.py`: also requires an active model for readiness.
- `tests/integration/test_transactions_api.py`: the app role also cannot update or delete decisions, or write `model_registry`.
- Load (not part of pytest): `load/transactions.js` via `make load`, results in BENCHMARK.md.

## Windows notes
- Windows Application Control on the primary PC blocks compiled wheels **inconsistently over time**: on 2026-11-16 confluent-kafka and numpy were blocked in the morning and loaded in the afternoon, while scikit-learn stayed blocked. Tests that need them skip when they cannot load, and `./scripts/dev.ps1 test-linux` always runs the complete suite (ADR-033). Last full runs: Linux 130 passed; Windows 129 passed, 1 skipped (scikit-learn).
- Hypothesis is pinned `<6.156`: newer releases ship a compiled extension that this PC's Application Control blocks (ADR-019).
- Testcontainers works with Docker Desktop as-is. If the Ryuk reaper fails on a machine, set `TESTCONTAINERS_RYUK_DISABLED=true` for that run only and clean up containers manually.
