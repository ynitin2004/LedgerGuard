# apps/worker/

One entrypoint, `uv run python -m apps.worker <role>`, whose role is chosen on the command line.

| Role | What | Status |
|---|---|---|
| `janitor` | Purge expired idempotency records in batches (`--once`, `--interval`, `--batch-size`) | **Implemented (M1)** |
| `outbox-publisher`, `feature-updater` | Outbox → Redpanda; Redis feature windows | Planned (M3) |
| `investigator` | LLM case summaries | Planned (M4) |
| `webhook-sender` | Signed webhook deliveries | Planned (M5) |
| `rag-ingest` | Corpus ingestion | Planned (M7) |
