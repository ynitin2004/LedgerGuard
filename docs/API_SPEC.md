# API Specification

> **Implemented (M0):** `GET /healthz`, `GET /readyz`, correlation-ID header, OpenAPI at `/docs` and `/openapi.json`.
> Endpoints under **Planned** are the agreed contract, not yet code.

## Conventions
- JSON only. Errors use **RFC 9457** `application/problem+json` (`type`, `title`, `status`, `detail`, `instance`, plus `correlation_id`) from M1.
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

## Planned
| Method & path | Auth | Purpose | Milestone |
|---|---|---|---|
| `POST /v1/transactions` | `X-API-Key` + `Idempotency-Key` | Create and evaluate a payment. Same key and body → stored response; same key with a different body → `409` | M1 (+decision M2) |
| `GET /v1/transactions/{id}` | `X-API-Key` (own tenant only) | Fetch transaction + decision | M1 |
| `GET /v1/transactions/{id}/decisions` | `X-API-Key` | Decision history with versions and reason codes | M2 |
| `GET /v1/investigations` | Supabase JWT (analyst/admin) | Case queue; filters: status, priority, date; cursor pagination | M5 |
| `GET /v1/investigations/{id}` | JWT | Case, evidence refs, AI summary with citations | M5 |
| `POST /v1/investigations/{id}/actions` | JWT | assign / approve / decline / escalate / note; audited | M5 |
| `POST /v1/webhooks` / `GET` / `DELETE` | `X-API-Key` | Register endpoints; deliveries signed with HMAC-SHA256 over `timestamp.body` | M5 |
| `/v1/admin/*` | JWT (admin) | API clients, model/policy activation | M5 |

### Idempotency semantics (M1)
- Canonical request hash = SHA-256 of JSON with sorted keys, no whitespace, upper-cased currency.
- Concurrent duplicates: the second INSERT waits on the PK until the first commits, then returns the stored response.
- TTL 24 h; after expiry a reused key is treated as new (documented behaviour).
