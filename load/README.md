# load/

k6 load tests, run from the pinned `grafana/k6:1.7.0` image (ADR-010); nothing is installed on the host.

```bash
make up migrate register-model
make seed NAME=k6                      # prints an API key; 2,000 customers with 30 days of history
make bench-up                          # API in a Linux container, 4 workers (ADR-022)
LOAD_API_KEY=<key> make load           # 200 RPS for 60 s; LOAD_RATE / LOAD_DURATION override
```
Windows: `./scripts/dev.ps1 seed -Name k6`, `./scripts/dev.ps1 bench-up`, `./scripts/dev.ps1 load -ApiKey <key>`.

- `transactions.js`: `POST /v1/transactions` at a constant arrival rate, with thresholds p99 < 100 ms and errors < 1%.
- `results/`: scratch output (git-ignored).
- `benchmarks/<date>-<milestone>/`: k6 summaries behind every number in docs/BENCHMARK.md.
