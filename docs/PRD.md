# Product Requirements — LedgerGuard AI

> Status: requirements are **settled** (Final Project Plan v2). Implementation status per milestone lives in [TASKS.md](TASKS.md).

## Problem
Payment platforms must decide approve, decline or review in milliseconds, record every decision reproducibly, and help analysts investigate suspicious payments. The hard parts are consistency under concurrency and failure, not the model.

## What LedgerGuard is
A **simulated** payment-risk intelligence platform. All transactions, customers and labels are synthetic. No real money, cards or personal data.

- **Deterministic financial state.** Rules + a versioned ML model + an explicit policy decide each transaction inside one atomic Postgres transaction. The same inputs and versions always reproduce the same decision.
- **Advisory AI.** An LLM writes investigation summaries that cite persisted evidence IDs. It never changes a decision and never blocks the hot path.

## Users
| User | Need |
|---|---|
| API client (merchant platform) | Submit a payment once, even with retries, and get a decision fast |
| Risk analyst | Work a queue of review/decline cases with evidence and an AI summary; record outcomes |
| Admin | Manage API clients, webhooks, model/policy versions |
| Reviewer of this repo | Verify every claim from tests, scripts and dashboards |

## Functional requirements (MVP = M0–M6)
1. `POST /v1/transactions` with an `Idempotency-Key`: the same key and body return the same response; the same key with a different body returns 409. Concurrency-safe (M1).
2. A risk decision per transaction from versioned rules, model and thresholds, with reason codes and a feature snapshot stored (M2).
3. A transactional outbox → Redpanda → idempotent consumers with retry, DLQ and replay (M3).
4. An investigation worker: SQL evidence, LLM structured output, evidence-ID validation, budget caps (M4).
5. Analyst case queue and actions with Supabase JWT auth and an append-only audit trail; signed webhooks with backoff (M5).
6. A failure matrix with tests, k6 load results and Grafana dashboards (M6).

## Non-functional requirements
| Requirement | Target | Proven by |
|---|---|---|
| Hot-path latency | p99 < 100 ms at 200 RPS, local | k6 → BENCHMARK.md (M2, M6) |
| Idempotency | 50 concurrent same-key requests → exactly 1 row | Testcontainers test (M1) |
| Event delivery | At-least-once publish, exactly-once *effect* | Chaos test with count reconciliation (M3) |
| Money | Integer minor units + ISO currency; no floats | Schema CHECKs and property tests (M1) |
| Reproducibility | Replaying a snapshot reproduces the decision | Replay test (M2) |
| AI safety | Fabricated evidence IDs rejected; LLM failure leaves the transaction untouched | Mock-LLM tests (M4) |

## Out of scope for the MVP
RAG/pgvector (M7–8), dashboard (M9), hosted deploy (M10), model retraining, multi-currency FX, chargeback feedback loop, multi-region.

## Success criteria
Each milestone meets its acceptance criteria in TASKS.md. The MVP is tagged `v0.1.0` at the end of M6.
