# Benchmarks

**No benchmark results exist yet.** This file only ever contains numbers produced by scripts in `load/`, with the environment and method recorded next to them. Numbers are never estimated or copied from elsewhere.

## Targets (from the plan)
| Scenario | Target | Milestone |
|---|---|---|
| `POST /v1/transactions` hot path, local | 200 RPS, p99 < 100 ms, error rate < 0.1 % | M2 (first run), M6 (suite) |
| Retrieval baseline (tsvector) recall@6 | measured, no target | M7 |
| RAG recall@6 vs baseline | improvement recorded; CI fails on > 3-pt regression | M8 |

## Required record for every run
- Git commit SHA, date, script path and arguments
- Host: CPU, RAM, OS, Docker resources; services co-located?
- Warm-up, duration, arrival rate / VUs
- RPS achieved, p50 / p95 / p99, error rate, per-stage breakdown (from metrics)
- Anything abnormal (GC pauses, throttling, background load)

## Environment facts already measured (not benchmarks)
- 2026-11-05, Windows 11 dev host, Docker Desktop with 7.6 GiB allocated to Docker: opening a new asyncpg connection to `localhost` took 2.04 s versus 0.01 s for `127.0.0.1` (IPv6-first resolution). This is why all URLs use 127.0.0.1 (ADR-009).

## Results
_None yet. The first entry arrives with M2._
