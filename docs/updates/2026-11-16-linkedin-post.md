Over the last few weeks I've been building **LedgerGuard**, a payment-risk platform, as a hands-on exercise in building backend systems that hold up under failure.

Every payment gets a decision in a single Postgres transaction from versioned rules, a logistic-regression model and explicit thresholds. Here's where it stands after 3 milestones:

🔁 **Safe retries:** 50 concurrent requests with the same idempotency key produce exactly 1 payment row. A Postgres primary key does the work, with no app-level locks.

🧮 **Reproducible decisions:** each decision stores its inputs and versions. 143,464 of 143,464 stored decisions recompute exactly.

⚡ **Speed:** 200 requests/second with p50 ≈ 13 ms and p99 ≈ 52–56 ms on one machine (k6, 3 runs, 0 errors).

📨 **Events that survive crashes:** a transactional outbox feeds Redpanda, and every consumer deduplicates through an inbox table. I force-killed the publisher and then the consumer mid-load: 36,002 events, all published and processed exactly once, and the payment API never noticed.

📈 **Measured, not guessed:** my first consumer handled only 80–160 events/s while 200/s arrived, so it fell behind. Batching its database work brought it to 754 events/s per copy, and 1,419 with three copies.

Stack: Python, FastAPI, Postgres, Redis, Redpanda/Kafka, scikit-learn, OpenTelemetry, Docker, GitHub Actions. All data is synthetic, and every number above comes from a script in the repo.

Next up: an AI investigator that writes summaries for human analysts. It is advisory only, kept off the payment path, and may cite only evidence that exists.

🔗 github.com/ynitin2004/LedgerGuard

#SystemDesign #DistributedSystems #Python #Kafka #PostgreSQL #BackendEngineering
