# Model Card — LedgerGuard risk model

> **Status: no model exists yet (planned for M2).** This card fixes what will be measured and reported. Every metric will come from `scripts/train_model.py` output; none are filled in ahead of time.

## Intended use
Score a simulated payment's fraud probability as **one input** to the decision policy, together with rules and explicit thresholds. The model never decides alone: `combined = clamp(model_prob + Σ soft-rule weights)`, and hard rules decline regardless.

## Not intended for
Real payments, real customers, or any claim about real-world fraud performance. **All training and evaluation data is synthetic.**

## Data (planned)
- Source: `scripts/gen_synthetic.py`, which seeds 30 days of history per customer with labelled fraud patterns (velocity bursts, amount spikes, new merchant, category shift, repeated failures).
- Split: time-based train / validation / held-out test, so no future data leaks into training.
- Features: exactly the `feature_snapshot` the rules use, so online and offline features match by construction.

## Model (planned)
- Baseline: logistic regression (scikit-learn), with calibrated probabilities.
- Challenger: gradient boosting, adopted **only** if PR-AUC improves by ≥ 0.05 on the held-out set (ADR-005).
- Registered in `model_registry` (`model_version`, algorithm, trained_at, metrics, artifact path, active flag).

## Metrics to report (on the held-out synthetic set)
| Metric | Value |
|---|---|
| PR-AUC | _not yet measured_ |
| Precision / recall at T_review and T_decline | _not yet measured_ |
| False-positive rate at T_decline | _not yet measured_ |
| Calibration (Brier score) | _not yet measured_ |

## Limitations and risks
Synthetic patterns are easier than real fraud, so expect optimistic metrics. There is no concept drift or adversarial adaptation. Retraining and the chargeback feedback loop are out of MVP scope.

## Versioning
`policy_version = rules@x.y+model@vN+thresholds@z` is stored on every decision. Replaying a stored feature snapshot with the same versions must reproduce the decision (replay test, M2).
