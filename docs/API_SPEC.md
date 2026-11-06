# API Specification

> **Implemented:** M0 `GET /healthz`, `GET /readyz`, correlation-ID header, OpenAPI at `/docs` and `/openapi.json`.
> M1 `POST /v1/transactions`, `GET /v1/transactions/{id}`, API-key auth, idempotency, RFC 9457 errors.
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

### `POST /v1/transactions` (M1; risk decision added in M2)
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

Response (M1: `status` is `created`; `decision`, `risk_score` and `policy_version` stay `null` until M2):
```json
{"id": "700e9dbd-…", "status": "created", "merchant_id": "m_42", "merchant_category": "5411", "customer_token": "cus_abc",
 "amount_minor": 1250, "currency": "USD", "decision": null, "risk_score": null, "policy_version": null,
 "created_at": "2026-11-06T05:00:39.249303+00:00"}
```

### `GET /v1/transactions/{id}` (M1)
Same body as above. `404` (`urn:ledgerguard:problem:transaction-not-found`) when the id does not exist **or belongs to another client**; the two cases are indistinguishable.

## Planned
| Method & path | Auth | Purpose | Milestone |
|---|---|---|---|
| `POST /v1/transactions` (risk evaluation) | `X-API-Key` + `Idempotency-Key` | Same endpoint; fills `decision`, `risk_score`, versions | M2 |
| `GET /v1/transactions/{id}/decisions` | `X-API-Key` | Decision history with versions and reason codes | M2 |
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
