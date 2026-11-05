# Testing Strategy

## Layers
| Suite | Location | Needs | Status |
|---|---|---|---|
| Unit | `tests/unit/` | nothing | **M0: health/readiness semantics, correlation IDs** |
| Integration | `tests/integration/` (marker `integration`) | Docker (Testcontainers) | **M0: migrations gate readiness on real PG16** |
| Property | Hypothesis, in unit/ | nothing | Planned (M1: canonical hashing, money) |
| Concurrency | `tests/concurrency/` | Docker | Planned (M1: 50 concurrent same-key → 1 row) |
| Failure | `tests/failure/` | Docker | Planned (M3/M6: FAILURE_MATRIX rows) |
| Contract | `tests/contract/` | nothing | Planned (M3: event schema compatibility) |
| Evals | `tests/evals/` | corpus | Planned (M8: retrieval + faithfulness gate) |
| Load | `load/` (k6 via Docker) | stack | Planned (M2/M6) |

## Principles
- Test behaviour at boundaries, not implementation details. Inject dependencies through `create_app(settings, probes=...)` and constructor arguments, not monkeypatching.
- Integration tests use the **same Postgres image as compose** (`pgvector/pgvector:0.8.0-pg16`).
- Tests never read the developer's `.env`. `tests/helpers.py:make_settings()` builds isolated settings and points optional dependencies at closed ports.
- No production credentials anywhere. The LLM is mocked in tests (M4).
- Report real results; a skipped or failing test is never reported as passing.

## Commands (verified 2026-11-05 on Windows 11, Docker Desktop)
```powershell
uv run python -m pytest                       # all: 14 passed (~42 s, includes container start)
uv run python -m pytest -m "not integration"  # unit only: 13 passed
./scripts/dev.ps1 test              # same as `make test`
```

## M0 test inventory
- `tests/unit/test_health.py`: `/healthz` is 200 with every dependency down; `/readyz` ready, degraded (optional failing → 200), not_ready (required failing → 503), and probe timeout.
- `tests/unit/test_correlation.py`: valid IDs are echoed, missing IDs get a UUID, unsafe IDs (CRLF injection, spaces, >128 chars, markup) are replaced.
- `tests/integration/test_db_readiness.py`: a fresh DB → 503 "migrations not applied"; `upgrade head` → 200 with `revision=0001`; `downgrade base` → 503; re-upgrade → 200.

## Windows notes
- Testcontainers works with Docker Desktop as-is. If the Ryuk reaper fails on a machine, set `TESTCONTAINERS_RYUK_DISABLED=true` for that run only and clean up containers manually.
