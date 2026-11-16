# API Specification

> **Implemented:** M0 `GET /healthz`, `GET /readyz`, correlation-ID header, OpenAPI at `/docs` and `/openapi.json`.
> M1 `POST /v1/transactions`, `GET /v1/transactions/{id}`, API-key auth, idempotency, RFC 9457 errors.
> M2 risk decision on every `POST`, `GET /v1/transactions/{id}/decisions`, rate limiting (`429`), `503` when no risk engine is available.
> Endpoints under **Planned** are the agreed contract, not yet code.

## Conventions
- JSON only. Every error is **RFC 9457** `application/problem+json`: `type`, `title`, `status`, `detail`, `instance`, `correlation_id` (plus `errors` on 422). `type` is `about:blank` for generic HTTP errors or a stable URN, `urn:ledgerguard:problem:<name>`. 500s never include exception text.
- `X-Correlation-ID`: optional on requests (`[A-Za-z0-9._-]{1,128}`; anything else is replaced). Always returned on responses. Bound into every log line and trace span.
- Money in requests and responses: `amount_minor` (integer > 0) + `currency` (ISO 4217, upper-cased by the server).
- Versioned under `/v1`. Pagination is cursor-based (`?cursor=&limit=`, max 100) from M5.

## Implemented

### `GET /healthz` — liveness
`200 {"status":"ok"}`. Checks no dependencies.

### `GET /readyz` — readiness
```json
{
  "status": "ready | degraded | not_ready",
  "checks": [
    {"name": "postgres", "ok": true,  "required": true,  "latency_ms": 4.4,  "detail": "revision=0001"},
    {"name": "redis",    "ok": false, "required": false, "latency_ms": 0.2,  "detail": "ConnectionError: ..."},
    {"name": "redpanda", "ok": true,  "required": false, "latency_ms": 13.8, "detail": "brokers=1"}
  ]
}
```
`200` for `ready` or `degraded`, `503` for `not_ready` (Postgres down or schema not at head). See ADR-011.

### Authentication (M1)
`X-API-Key: lg_...` on every `/v1` request. Missing, unknown and revoked keys all get the same `401` problem (`urn:ledgerguard:problem:unauthenticated`, `WWW-Authenticate: ApiKey`). Keys are created with `scripts/create_api_client.py` and stored only as SHA-256 hashes.

### `POST /v1/transactions` (M1; evaluated by the risk engine since M2)
Headers: `X-API-Key`, `Idempotency-Key` (required, 1–255 printable ASCII characters, no spaces).
```json
{"merchant_id": "m_42", "merchant_category": "5411", "customer_token": "cus_abc", "amount_minor": 1250, "currency": "usd"}
```
| Field | Rule |
|---|---|
| `merchant_id`, `customer_token` | `[A-Za-z0-9_-]{1,64}`; the token is opaque, never a card number |
| `merchant_category` | 4-digit MCC (ISO 18245) |
| `amount_minor` | JSON integer, 1 to 2^63−1. Floats and numeric strings are rejected |
| `currency` | 3 letters, any case; stored upper-case |

Unknown fields are rejected.

| Status | When |
|---|---|
| `201` | Created. Body = transaction (below). A replay returns the stored `201` body plus `Idempotent-Replayed: true` |
| `401` | Missing / invalid / revoked API key |
| `409` | `urn:ledgerguard:problem:idempotency-key-reused`: same key, different body |
| `422` | `urn:ledgerguard:problem:validation-error`, including a missing or malformed `Idempotency-Key` |
| `429` | `urn:ledgerguard:problem:rate-limited`, `Retry-After: 1` (per-client requests per second) |
| `503` | `urn:ledgerguard:problem:risk-engine-unavailable`, `Retry-After: 5` (e.g. no active model). Nothing is written, so retry with the same key |

Response. The payment is evaluated in the same database transaction, so `status` is `evaluated` and `decision` is always set (M2):
```json
{"id": "700e9dbd-…", "status": "evaluated", "merchant_id": "m_42", "merchant_category": "5411", "customer_token": "cus_abc",
 "amount_minor": 1250, "currency": "USD", "decision": "review", "risk_score": "0.35002", "reason_codes": ["VELOCITY_5M"],
 "policy_version": "rules@1.0+model@v1+thresholds@1.0", "created_at": "2026-11-16T08:20:39.249303+00:00"}
```
`risk_score` is the combined score as an exact 5-decimal string. `reason_codes` lists the rule codes that fired. A replayed response is the stored original, decision included.

### `GET /v1/transactions/{id}` (M1)
Same body as above. `404` (`urn:ledgerguard:problem:transaction-not-found`) when the id does not exist **or belongs to another client**; the two cases are indistinguishable.

### `GET /v1/transactions/{id}/decisions` (M2)
Decision history for one transaction, oldest first. Decisions are never modified: an analyst override (M5) adds a row. `404` rules as above.
```json
[{"id": "92e5a229-…", "transaction_id": "700e9dbd-…", "decision": "review", "score": "0.35002", "model_score": "0.00002",
  "reason_codes": ["VELOCITY_5M"], "rules_version": "1.0", "model_version": "v1", "thresholds_version": "1.0",
  "policy_version": "rules@1.0+model@v1+thresholds@1.0",
  "feature_snapshot": {"amount_minor": 300, "log_amount": 5.70711, "velocity_5m": 4, "velocity_1h": 4, "history_count_30d": 4,
                       "amount_zscore": 0.0, "is_new_merchant": 0, "category_share_30d": 1.0, "failed_24h": 0},
  "evidence_refs": ["92e5a229-…:VELOCITY_5M"], "created_at": "…"}]
```
`feature_snapshot` plus the three versions reproduce `decision`, `score` and `model_score` exactly (`scripts/verify_decisions.py`). `evidence_refs` are the stable IDs the investigator (M4) may cite (plan A11).

### Rate limiting (M2, ADR-025)
Per API client, fixed one-second window, `LG_RATE_LIMIT_PER_SECOND` (default 1000). Applies to every `/v1` route. Fails open when Redis is unavailable.

## Planned
| Method & path | Auth | Purpose | Milestone |
|---|---|---|---|
| `GET /v1/investigations` | Supabase JWT (analyst/admin) | Case queue; filters: status, priority, date; cursor pagination | M5 |
| `GET /v1/investigations/{id}` | JWT | Case, evidence refs, AI summary with citations | M5 |
| `POST /v1/investigations/{id}/actions` | JWT | assign / approve / decline / escalate / note; audited | M5 |
| `POST /v1/webhooks` / `GET` / `DELETE` | `X-API-Key` | Register endpoints; deliveries signed with HMAC-SHA256 over `timestamp.body` | M5 |
| `/v1/admin/*` | JWT (admin) | API clients, model/policy activation | M5 |

### Idempotency semantics (implemented M1, ADR-018)
- Keys are scoped per API client: two clients may use the same key independently.
- Canonical request hash = SHA-256 of the validated body as JSON with sorted keys, no whitespace, upper-cased currency. Field order and currency case therefore do not matter.
- The key is claimed, the transaction and audit event are written, and the response is stored in **one** Postgres transaction. A crash leaves nothing behind, so a retry is always safe.
- Concurrent duplicates: the claim is `INSERT … ON CONFLICT DO UPDATE … WHERE expired`. A duplicate waits on the primary key until the first request commits, then returns the stored response. No application locks are used.
- TTL 24 h (`LG_IDEMPOTENCY_TTL_S`). After expiry a reused key is treated as a new request. `python -m apps.worker janitor` purges expired keys; correctness does not depend on it.
- Replays return the same JSON document. Key order may differ, because the stored copy is `jsonb`.
