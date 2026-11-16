# Benchmarks

This file only ever contains numbers produced by scripts in `load/`, with the environment and method recorded next to them. Numbers are never estimated or copied from elsewhere.

## Targets (from the plan)
| Scenario | Target | Milestone | Status |
|---|---|---|---|
| `POST /v1/transactions` hot path, local | 200 RPS, p99 < 100 ms, error rate < 0.1 % | M2 (first run), M6 (suite) | **Met (M2), 3 of 3 runs** |
| Retrieval baseline (tsvector) recall@6 | measured, no target | M7 | — |
| RAG recall@6 vs baseline | improvement recorded; CI fails on > 3-pt regression | M8 | — |

## Results

### 2026-11-16: M2 hot path, 200 RPS (acceptance run)
| Run | Requests | Achieved RPS | Errors | p50 | p90 | p95 | **p99** | max |
|---|---|---|---|---|---|---|---|---|
| 1 | 12,002 | 200.0 | 0 | 10.28 ms | 13.76 ms | 16.51 ms | **44.03 ms** | 202.73 ms |
| 2 | 12,001 | 200.0 | 0 | 10.86 ms | 14.93 ms | 17.90 ms | **41.75 ms** | 80.47 ms |
| 3 | 12,002 | 200.0 | 0 | 11.29 ms | 17.64 ms | 24.03 ms | **59.09 ms** | 156.57 ms |

Raw k6 summaries: `load/benchmarks/2026-11-16-m2/run{1,2,3}.json`.

**What each request does:** API-key check (cached in-process), rate limit (Redis), idempotency claim, read 30 days of customer history, velocity windows (Redis, Lua), features, 7 rules, LR model, policy, then insert the transaction, risk decision and audit event, store the idempotent response, commit, and record the payment in the Redis window. OpenTelemetry export to the collector stays on.

**Method:**
- Script: `load/transactions.js` (k6 `constant-arrival-rate`, 200 iterations/s for 60 s, 100–400 VUs), run as `docker compose --profile bench run --rm --no-deps k6 run --summary-export … /load/transactions.js`. That's what `make load` / `./scripts/dev.ps1 load` do.
- API: `infra/api/Dockerfile` image, uvicorn with **4 workers**, compose profile `bench` (`make bench-up`). Postgres 16, Redis 7.4, Redpanda and the OTel collector come from `docker-compose.yml`. k6, API and data stores share one Docker network, so no host port forwarding is in the path.
- Data: `scripts/seed_history.py --name k6 --customers 2000` seeded 93,504 history rows (30 days); k6 picks random customers `cus_00000..cus_01999`, random merchants, amounts 5.00–99.99 USD and a fresh Idempotency-Key per request.
- Runs back to back on one warm API container, after one warm-up run.
- Code: commit `960d866` plus the M2 changes (committed together with these results).

**Host:** Intel Core i7-14700 (20 cores / 28 threads), 15.7 GB RAM, Windows 11 Pro (build 26200), Docker Desktop 29.9.2 on WSL2 (kernel 5.15.167.4), Docker VM with 28 CPUs and 7.6 GiB RAM. Every service ran on this one machine.

**Afterwards:** `scripts/verify_decisions.py` replayed all 143,464 decisions in the database (benchmark plus earlier runs) with 0 mismatches.

**Caveats:**
- The decision mix under this load is **not representative**: 92% decline. 200 RPS over 2,000 customers gives each customer several payments a minute, which is exactly the velocity pattern the rules flag. This benchmark measures latency and throughput, not fraud rates; see MODEL_CARD.md for those.
- During the runs the API logged 6 rate-limiter Redis timeouts (50 ms budget). Each one failed open as designed.

### What did not work first (same day, kept for the record)
| Attempt | Result | Cause | Fix |
|---|---|---|---|
| API on the Windows host, 1 uvicorn worker | 132 RPS achieved (3,771 iterations dropped), p99 8.9 s, min 72 ms | One process is CPU-bound at about 10 ms of Python per request | Several workers |
| API on the Windows host, 4 uvicorn workers | p99 109 ms; 25 of 12,001 requests hung until k6's 60 s timeout | uvicorn workers on Windows sometimes fail `listen()` on the shared socket (`OSError [WinError 10022]`) and are restarted; killing a parent leaves orphaned workers accepting connections | Benchmark the API in a Linux container (ADR-022). Added bounded DB timeouts (`command_timeout`, `pool_timeout` 5 s) so a stalled query fails fast instead of hanging |
| Linux container, before optimisation | p99 49 / 156 / 67 ms (one run over target) | Each request made about 17 network round trips, including a separate auth transaction | In-process cache for API-key lookups (10 s TTL, ADR-024) and `now()` returned by the idempotency claim: about 5 fewer round trips per request |

## Environment facts (not benchmarks)
- 2026-11-05, Windows 11 dev host: opening a new asyncpg connection to `localhost` took 2.04 s versus 0.01 s for `127.0.0.1` (IPv6-first resolution). This is why all URLs use 127.0.0.1 (ADR-009).
- 2026-11-16, same host: a Postgres `SELECT 1` round trip from the host through Docker Desktop's port forwarding averaged 0.37 ms over 100 calls.

## Required record for every run
- Git commit, date, script path and arguments
- Host: CPU, RAM, OS, Docker resources; which services were co-located
- Warm-up, duration, arrival rate / VUs
- Achieved RPS, p50 / p95 / p99, error rate (per-stage breakdown arrives with M6 metrics)
- Anything abnormal
