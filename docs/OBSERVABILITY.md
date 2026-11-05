# Observability

> **Implemented (M0):** structured JSON logs with correlation IDs, OTel traces + HTTP metrics exported to the collector, Prometheus scraping, and a provisioned Grafana datasource.
> **Planned (M6):** dashboards, alerts, stage-level latency, and a trace backend.

## Pipeline (ADR-012)
```
API/workers --OTLP/HTTP :4318--> otel-collector --:8889--> Prometheus :9090 --> Grafana :3000
                                         \--> debug exporter (traces, until M6)
Redpanda :9644/public_metrics -----------------> Prometheus
```
Verified 2026-11-05: all Prometheus targets `up` (otel-collector-app-metrics, otel-collector-self, redpanda, prometheus). After traffic, Prometheus held `ledgerguard_http_server_duration_milliseconds_*`, `ledgerguard_http_server_active_requests` and `ledgerguard_http_server_response_size_bytes_*`.

## Logs
- structlog with ISO-UTC timestamps, level and bound context. `LG_LOG_FORMAT=json` for machines, `console` for local reading.
- `correlation_id` is bound per request by `CorrelationIdMiddleware` and is present on every log line in that request.
- Never log secrets, API keys, full request bodies or LLM prompts containing evidence (M4 logs prompt *version* + token counts only).

## Traces
FastAPI auto-instrumentation creates a server span per request; the correlation ID is added as a span attribute. `/healthz` and `/readyz` are excluded. Planned spans: idempotency, features, rules, model, policy, commit (M2); outbox publish/consume with context propagated in Kafka headers (M3); LLM calls with token and cost attributes (M4).

## Metrics — planned catalogue
| Metric | Type | Milestone |
|---|---|---|
| HTTP server duration / active requests | histogram / gauge | **M0 (auto)** |
| `decision_stage_duration_ms{stage}` | histogram | M2 |
| `decisions_total{decision,policy_version}` | counter | M2 |
| `outbox_backlog` (unpublished rows), `outbox_publish_lag_ms` | gauge / histogram | M3 |
| consumer lag per group (Redpanda), `dlq_messages_total` | gauge / counter | M3 |
| `llm_tokens_total`, `llm_cost_usd_total`, `llm_budget_exhausted_total` | counter | M4 |
| `webhook_delivery_attempts_total{status}` | counter | M5 |
| `redis_fallback_total{feature}` | counter | M6 |

## Dashboards (M6)
p50/p95/p99 by hot-path stage, decision mix, outbox backlog, consumer lag, DLQ, LLM cost and budget, and error rate. Screenshots go in the README.
