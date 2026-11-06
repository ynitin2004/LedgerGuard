# Data Model

> **Implemented:** `0001` baseline (M0). `0002` (M1): `api_clients`, `transactions`, `idempotency_records`, `audit_events` and the `ledgerguard_app` role with its grants.
> Tables marked with a later milestone are **Planned**. They are listed so reviews can check migrations against the agreed design.

## Conventions
- UUID primary keys (`gen_random_uuid()`, built into PG13+). `created_at timestamptz NOT NULL DEFAULT now()` everywhere.
- **Money:** `amount_minor BIGINT` + `currency CHAR(3)`, with `CHECK (amount_minor > 0)`. No floats in authoritative data.
- Constraint names come from the naming convention in `infrastructure/db/base.py` (`pk_*`, `fk_*`, `uq_*`, `ck_*`, `ix_*`).
- Multi-tenant columns from day 1; in the MVP each API client is one tenant.
- Enumerations are `text` + `CHECK` constraints (cheaper to evolve than PG enum types).
- Append-only tables (`audit_events`, `case_actions`): the app DB role gets INSERT/SELECT only (`REVOKE UPDATE, DELETE, TRUNCATE`).

## Database roles (implemented M1, ADR-017)
| Role | Used by | Privileges |
|---|---|---|
| schema owner (`POSTGRES_USER`, `LG_MIGRATION_DATABASE_URL`) | Alembic, `scripts/create_api_client.py` | Everything (owns the tables) |
| `ledgerguard_app` (`LG_DATABASE_URL`) | API, workers | `USAGE` on schema; `SELECT` alembic_version, api_clients; `SELECT, INSERT` transactions, audit_events; `SELECT, INSERT, UPDATE, DELETE` idempotency_records. No DDL |

Each migration grants exactly what the code it ships needs; there are no default privileges. The migration creates the role `NOLOGIN` if it is missing. The login password is per environment (local: `infra/postgres/init/10-app-role.sh` on a new volume, or `make db-role` / `./scripts/dev.ps1 db-role` on an existing one).

## Migration workflow
1. Change or add ORM models (from M1) under `infrastructure/db/`.
2. `uv run python -m alembic -c migrations/alembic.ini revision --autogenerate -m "<what>"`, then **review and edit** the generated file. Autogenerate is a draft.
3. Migrations must be backward compatible with the running code (expand → migrate → contract). There are no destructive changes in the same release that stops using a column.
4. `upgrade head`, `downgrade -1` and `upgrade head` must all succeed (the integration test runs a full round trip).
5. Each revision runs in its own transaction (`transaction_per_migration=True`).

## Planned tables
| Table | Key columns | Milestone |
|---|---|---|
| `api_clients` | id, tenant_id, name, key_hash CHAR(64) UNIQUE (SHA-256), status CHECK (active/revoked) | **M1 ✅** |
| `transactions` | id, client_id FK, merchant_id, merchant_category CHAR(4), customer_token, amount_minor BIGINT CHECK > 0, currency CHAR(3) CHECK `^[A-Z]{3}$`, status CHECK (`created→evaluated→settled_sim/failed_sim`), decision CHECK (approve/decline/review, nullable), risk_score numeric(6,5), policy_version, rules_version, model_version, correlation_id; idx (client_id, created_at desc), (customer_token, created_at desc), (merchant_id, created_at desc) | **M1 ✅** (risk cols filled M2) |
| `idempotency_records` | PK (client_id FK, key varchar(255)), request_hash CHAR(64), response_status, response_body jsonb, created_at, expires_at (24 h); idx expires_at | **M1 ✅** |
| `audit_events` | id, actor, action, resource_type, resource_id, correlation_id, metadata jsonb; idx (resource_type, resource_id); append-only | **M1 ✅** |
| `risk_decisions` | id, transaction_id FK, rules_version, model_version, thresholds_version, score, decision, reason_codes jsonb, feature_snapshot jsonb, evidence_refs jsonb | M2 |
| `model_registry` | model_version PK, algo, trained_at, metrics jsonb, artifact_path, active | M2 |
| `outbox_events` | event_id PK, aggregate_type, aggregate_id, event_type, schema_version, payload jsonb, published_at NULL, attempt_count, last_error; partial idx WHERE published_at IS NULL | M3 |
| `consumer_inbox` | PK (consumer_name, event_id), processed_at | M3 |
| `investigation_cases` | id, transaction_id UNIQUE, tenant_id, priority, status, summary, evidence_refs, llm_model, prompt_version, attempts, last_error, assigned_to, updated_at; idx (status, priority, created_at) | M4 |
| `case_actions` | id, case_id FK, actor_id, action, note, correlation_id; append-only | M5 |
| `webhook_endpoints` | id, client_id FK, url, secret_hash, events text[], status | M5 |
| `webhook_deliveries` | id, endpoint_id FK, event_id, attempt, status, http_status, next_retry_at, last_error; UNIQUE (endpoint_id, event_id, attempt) | M5 |
| `documents`, `chunks` (embedding vector(1024), tsv tsvector), `eval_runs` | — | M7–8 (pgvector only if ADR-006 gate passes) |

## Key invariants (enforced from M1)
- One business transaction writes `transactions`, `risk_decisions`, `audit_events`, `outbox_events` and updates `idempotency_records` atomically.
- A decision row is never mutated. Analyst overrides add a new `risk_decisions` row.
- Idempotency races are resolved by the primary key on `(client_id, key)`, not by application locks.
