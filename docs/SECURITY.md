# Security

> **Implemented (M0):** secret hygiene, localhost-only port bindings, input validation of correlation IDs, CI without credentials.
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
| Stolen API key | Hashed at rest, per-client status (revocable), rate limiting | Planned (M1, rate limit M1/M2) |
| Cross-tenant data access | Every query scoped by `client_id`/`tenant_id`; negative tests | Planned (M1, M5) |
| Replayed / duplicated requests | Idempotency keys scoped by client, request-hash mismatch → 409 | Planned (M1) |
| Tampering with audit trail | Append-only tables; app role lacks UPDATE/DELETE | Planned (M1) |
| Analyst privilege escalation | Supabase JWT verification (issuer, audience, expiry), role from `app_metadata` only | Planned (M5) |
| Webhook spoofing / replay | HMAC-SHA256 over `timestamp.body`, timestamp tolerance window | Planned (M5) |
| SSRF via webhook URLs | HTTPS only, block private/link-local ranges, no redirects | Planned (M5) |
| **Prompt injection** (merchant names, notes in evidence) | LLM is advisory only; evidence passed as delimited data; structured output schema; every cited ID validated against persisted evidence; no tools/actions given to the LLM | Planned (M4) |
| LLM cost abuse | Per-case token cap, max 2 retries, daily budget in Redis → `needs_review` | Planned (M4) |
| Dependency vulnerabilities | Locked `uv.lock`; Dependabot/pip-audit in CI | Planned (M6) |

## Data
All data is synthetic. `customer_token` is an opaque token, not a PAN or personal identifier.
