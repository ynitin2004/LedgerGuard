# Security

> **Implemented (M0):** secret hygiene, localhost-only port bindings, input validation of correlation IDs, CI without credentials.
> **Implemented (M1):** hashed API keys, tenant-scoped reads, idempotency conflict detection, append-only audit trail via a least-privilege DB role, strict input validation, problem+json without internals.
> Threat-model mitigations marked **Planned** arrive with the milestone shown.

## Secrets
- `.env` is git-ignored. `.env.example` holds **dev-only** values for the local stack, which must never be reused in hosted environments.
- Compose requires secrets from `.env` (`${VAR:?}`): no defaults are baked into images.
- CI uses no secrets (Testcontainers creates throwaway databases).
- Hosted secrets come from AWS SSM Parameter Store (M10). The AgentRouter key lives only in `.env` / SSM (M4).
- API keys are stored as SHA-256 hashes (M1). Webhook secrets are stored hashed and shown once (M5).

## Network exposure (local)
All compose ports bind to `127.0.0.1`, so nothing is reachable from the LAN. Grafana sign-up is disabled.

## Threat model (STRIDE-style, MVP)
| Threat | Mitigation | Status |
|---|---|---|
| Header injection via correlation ID | Strict charset/length; otherwise replaced with a UUID | **M0, tested** |
| Stolen API key | SHA-256 hash at rest (256-bit random keys, so no KDF needed), per-client status (revoked = 401, same response as unknown); rate limiting | **M1, tested**; rate limit Planned (M2) |
| Cross-tenant data access | Every query scoped by `client_id`; another client's transaction returns the same 404 as a missing one | **M1, tested** (analyst/JWT paths M5) |
| Replayed / duplicated requests | Idempotency keys scoped by client, request-hash mismatch → 409 | **M1, tested** |
| Tampering with audit trail | Append-only table; runtime role `ledgerguard_app` has INSERT/SELECT only, no DDL | **M1, tested** |
| Leaking internals in errors | problem+json; 500 bodies carry only a correlation ID; 422s echo field locations, never submitted values | **M1, tested** |
| Money precision / type confusion | `amount_minor` must be a JSON integer (floats, strings, booleans rejected); BIGINT + CHECK in the DB | **M1, tested** |
| Analyst privilege escalation | Supabase JWT verification (issuer, audience, expiry), role from `app_metadata` only | Planned (M5) |
| Webhook spoofing / replay | HMAC-SHA256 over `timestamp.body`, timestamp tolerance window | Planned (M5) |
| SSRF via webhook URLs | HTTPS only, block private/link-local ranges, no redirects | Planned (M5) |
| **Prompt injection** (merchant names, notes in evidence) | LLM is advisory only; evidence passed as delimited data; structured output schema; every cited ID validated against persisted evidence; no tools/actions given to the LLM | Planned (M4) |
| LLM cost abuse | Per-case token cap, max 2 retries, daily budget in Redis → `needs_review` | Planned (M4) |
| Dependency vulnerabilities | Locked `uv.lock`; Dependabot/pip-audit in CI | Planned (M6) |

## Data
All data is synthetic. `customer_token` is an opaque token, not a PAN or personal identifier.
