# Failure Matrix

Every row must end with a passing automated test or a scripted demo (M6 acceptance).
**Verified** = proven in this repo with the evidence named. Everything else is the agreed design, **planned**.

| # | Scenario | What happens | Persisted state | Retry safe? | Recovery | Evidence |
|---|---|---|---|---|---|---|
| F0a | Postgres down | `/readyz` → 503 (instance leaves LB); `/healthz` stays 200 (no restart loop) | unchanged | — | Automatic when PG returns | **Verified M0**: `tests/unit/test_health.py`, `tests/integration/test_db_readiness.py`, manual `docker compose stop postgres` |
| F0b | Schema behind code (migration not applied) | `/readyz` → 503 "schema at X, code expects Y" | unchanged | — | `alembic upgrade head` | **Verified M0**: integration test |
| F0c | Redis or Redpanda down | `/readyz` → 200 `degraded`; instance keeps serving | unchanged | — | Automatic | **Verified M0**: unit test + manual `docker compose stop redis` |
| F1 | Client retries after timeout | Same idempotency key → stored response | 1 transaction row | Yes | — | Planned M1 |
| F2 | Concurrent same-key requests | PG unique PK serialises; losers re-read stored response | 1 row; N identical responses | Yes | — | Planned M1 |
| F3 | Same key, different body | 409 problem+json | unchanged | n/a | Client uses a new key | Planned M1 |
| F4 | Redis down on hot path | Idempotency PG-only; velocity features from PG window query; rate limit fail-open + alert | Correct, slower | Yes | Automatic | Planned M2/M6 |
| F5 | Broker down | Hot path unaffected (outbox in PG); publisher retries; backlog alert | Outbox rows accumulate | Yes | Publisher drains backlog | Planned M3 |
| F6 | Publisher crashes between produce and `published_at` update | Event re-published | Duplicate on topic | Yes (consumers dedupe) | Restart | Planned M3 (chaos test with count reconciliation) |
| F7 | Consumer crashes mid-processing | PG txn rolled back; offset not committed; redelivered | Inbox row absent → reprocessed once | Yes | Restart | Planned M3 |
| F8 | Poison message | Retry topic with backoff ×5 → DLQ with reason | DLQ entry | After fix | `lg replay-dlq --event-id` | Planned M3 |
| F9 | LLM timeout / error / budget exhausted | Case → `needs_review` without summary; transaction untouched | Case row with last_error | Yes (max 2) | Analyst works the case manually | Planned M4 |
| F10 | LLM cites fabricated evidence | Output rejected by validator; retried, then `needs_review` | No invalid summary stored | Yes | — | Planned M4 |
| F11 | Webhook receiver down | Exponential backoff, max 5 attempts; deliveries recorded | `webhook_deliveries` rows | Yes (signed, idempotent by event_id) | Manual redeliver | Planned M5 |
| F12 | DB failover / connection reset mid-request | Request fails cleanly (5xx, no partial write); client retry hits idempotency | All-or-nothing | Yes | Pool `pre_ping` reconnects | Planned M6 |
