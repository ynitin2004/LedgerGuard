# apps/worker/

One entrypoint, `python -m apps.worker <role>`, whose role is chosen on the command line. Kafka roles run in the app image (`docker compose --profile workers up -d`), because confluent-kafka cannot load on the primary Windows PC (ADR-022).

| Role | What | Status |
|---|---|---|
| `janitor` | Purge expired idempotency records in batches (`--once`, `--interval`, `--batch-size`) | **Implemented (M1)** |
| `outbox-publisher` | Outbox → `txn.events` (SKIP LOCKED batches, idempotent producer; run as many as needed) | **Implemented (M3)** |
| `feature-updater` | `txn.events` → Redis velocity windows; batch inbox, retry/DLQ; scale up to 6 replicas | **Implemented (M3)** |
| `replay-dlq` | `--event-id <id>` or `--all`: send dead-lettered events back through the retry topic | **Implemented (M3)** |
| `reconcile` | Outbox vs topic vs inbox counts; `--strict` exits 1 unless exactly-once holds | **Implemented (M3)** |
| `investigator` | LLM case summaries | Planned (M4) |
| `webhook-sender` | Signed webhook deliveries | Planned (M5) |
| `rag-ingest` | Corpus ingestion | Planned (M7) |
