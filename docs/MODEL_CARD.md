# Model Card: LedgerGuard risk model `v1`

> **Status: implemented (M2).** Every number below is copied from the output of `scripts/train_model.py`, which also stores it in `models/lr-v1.json` (`metrics`). A CI test retrains the model and fails if the coefficients or these metrics differ from the committed artifact (`tests/unit/test_model_training.py`).

## Intended use
Score a simulated payment's fraud probability as **one input** to the decision policy, together with rules and explicit thresholds. The model never decides alone: `combined = clamp(model_score + Σ soft-rule weights, 0, 1)`, and hard rules decline regardless of the score.

## Not intended for
Real payments, real customers, or any claim about real-world fraud performance. **All training and evaluation data is synthetic.**

## Data
- **Source:** `ledgerguard.synthetic.generate(SyntheticConfig(seed=7))`: 2,000 customers, 400 merchants, 30 days ending 2026-01-31, 94,345 transactions. Customers have home categories, favourite merchants, log-normal amounts and a daily rhythm. Legitimate noise is deliberate: new merchants, occasional big purchases, a 3% failure rate, and short bursts of several purchases. About 4% of customers suffer one fraud incident of one of three kinds:
  - `card_testing`: a burst of small payments at new merchants, many of them failing;
  - `account_takeover`: a few payments of 8–30× the customer's usual amount at risky merchant categories;
  - `fail_then_success`: repeated failures at one merchant, then a larger success.
- **Labels:** every incident payment is fraud (0.47% of all payments).
- **Split:** by time. Train on the first 21 days (65,293 rows, 0.352% fraud) and test on the last 9 days (29,052 rows, 0.719% fraud). No future data leaks into training.
- **Features:** each row's features come from `compute_features` over only the customer's strictly earlier payments (`ledgerguard.risk.dataset.build_rows`). The API uses the same function, so training and serving use one definition.

## Model
- **Logistic regression** (scikit-learn 1.9.1, lbfgs, C=1.0, no class weights) on standardised features: `log_amount`, `velocity_5m`, `velocity_1h`, `history_count_30d`, `amount_zscore`, `is_new_merchant`, `category_share_30d`, `failed_24h`. The raw `amount_minor` feeds rules only.
- **Exported as JSON:** feature order, means, scales, coefficients and intercept. The API scores with a dot product and a sigmoid in pure Python (`ledgerguard.risk.model`). There is no pickle and no numpy at runtime (ADR-020, ADR-021).
- **Parity:** the largest difference between the pure-Python score and scikit-learn's `predict_proba` on the test set is **4.4e-16**.
- **Challenger:** `HistGradientBoostingClassifier` reached a test PR-AUC of **0.5344** against **0.5358** for logistic regression. It is **not adopted**, because the plan requires an improvement of at least 0.05.
- **Registry:** `model_registry` holds `v1` with its full artifact and sha256 (`6fec2bfb…052d`). At most one model is active.

## Results (held-out last 9 days, synthetic)
| Metric | Value |
|---|---|
| PR-AUC (average precision) | **0.5358** |
| ROC-AUC | 0.9238 |
| Model alone at p ≥ 0.5: precision / recall / FPR | 0.889 / 0.383 / 0.035% (90 flagged) |

### Full decision policy (`rules@1.0+model@v1+thresholds@1.0`) on the same rows
| Outcome | Count | Precision | Recall (of fraud) | FPR |
|---|---|---|---|---|
| `decline` | 120 | 0.717 | 0.412 | 0.118% |
| `review` or `decline` | 641 | 0.209 | 0.641 | 1.758% |
| `approve` | 28,411 | — | — | — |

Rule hits on the test set: AMOUNT_ZSCORE 248, NEW_MERCHANT 202, CATEGORY_SHIFT 101, REPEAT_FAIL_24H 87, VELOCITY_5M 82, VELOCITY_1H 30. HIGH_AMOUNT_ABS never fires on this data: it is a business limit at 10,000.00, not a pattern detector.

**Reading these numbers:** the policy declines about 4 in 10 fraudulent payments, and 7 in 10 of its declines are fraud. Adding the review queue catches 64% of fraud, at the cost of sending 1.8% of legitimate payments to review. Thresholds (`policy/v1.0.yaml`: review 0.30, decline 0.80) and rule cut-offs were set from a profile of the generated data, not tuned on the test window.

## Limitations and risks
- Synthetic patterns are cleaner than real fraud, so expect optimistic metrics. No concept drift and no adversarial adaptation.
- **Calibration is not measured yet** (no Brier score). Probabilities come from an unweighted LR on 0.35% positives, so they are small; the policy compensates with rule weights.
- Amount features are per currency but thresholds assume 2-decimal currencies (no FX; out of MVP scope).
- Retraining and the chargeback feedback loop are out of MVP scope.

## Reproduce
```bash
make train                    # Linux/macOS/CI (needs numpy)
./scripts/dev.ps1 train       # Windows: runs in a Linux container (numpy is blocked on the dev PC, ADR-021)
make register-model           # register models/lr-v1.json and activate it
```
Training is deterministic: the same seed and locked dependency versions give the same coefficients and metrics. Only `training.trained_at` changes. A changed artifact must ship as a new version (`v2`): versions are immutable in the registry.

## Versioning
`policy_version = rules@x.y+model@vN+thresholds@z` is stored on every decision. Replaying a stored feature snapshot with the same versions reproduces the decision. `scripts/verify_decisions.py` replayed all 143,464 decisions made during the M2 benchmark with **0 mismatches**.
