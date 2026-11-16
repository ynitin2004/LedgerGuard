"""Train the logistic-regression risk model on synthetic data and export a JSON artifact.

    make train                      # Linux/macOS/CI
    ./scripts/dev.ps1 train         # Windows: runs inside a Linux container (ADR-021)

Deterministic: the same seed and locked dependency versions give the same artifact.
Writes ``models/lr-v1.json`` (coefficients + held-out metrics) and prints the metrics
that docs/MODEL_CARD.md quotes. Nothing here runs in the API.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from ledgerguard.infrastructure.risk_assets import load_policy, load_rules
from ledgerguard.risk.dataset import LabelledRow, build_rows
from ledgerguard.risk.engine import RiskEngine
from ledgerguard.risk.model import LogisticModel
from ledgerguard.synthetic import SyntheticConfig, generate

ROOT = Path(__file__).resolve().parents[1]
MODEL_FEATURES = (
    "log_amount",
    "velocity_5m",
    "velocity_1h",
    "history_count_30d",
    "amount_zscore",
    "is_new_merchant",
    "category_share_30d",
    "failed_24h",
)
TEST_DAYS = 9  # time-based split: the last 9 days are held out
GB_ADOPTION_MARGIN = 0.05  # plan s.7: gradient boosting only if PR-AUC improves by >= 0.05


def _matrix(rows: list[LabelledRow]) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    x = np.array([[r.features[f] for f in MODEL_FEATURES] for r in rows], dtype=np.float64)
    y = np.array([1 if r.is_fraud else 0 for r in rows], dtype=np.int64)
    return x, y


def _rates(pred: np.ndarray[Any, Any], y: np.ndarray[Any, Any]) -> dict[str, float]:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    return {
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
        "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
        "fpr": round(fp / (fp + tn), 5) if fp + tn else 0.0,
        "flagged": tp + fp,
    }


def train(seed: int, rules_version: str, policy_version: str) -> dict[str, Any]:
    config = SyntheticConfig(seed=seed)
    rows = build_rows(generate(config))
    cutoff = config.end - timedelta(days=TEST_DAYS)
    train_rows = [r for r in rows if r.created_at < cutoff]
    test_rows = [r for r in rows if r.created_at >= cutoff]
    x_train, y_train = _matrix(train_rows)
    x_test, y_test = _matrix(test_rows)

    scaler = StandardScaler().fit(x_train)
    lr = LogisticRegression(C=1.0, max_iter=2000).fit(scaler.transform(x_train), y_train)
    p_test = lr.predict_proba(scaler.transform(x_test))[:, 1]
    lr_pr_auc = float(average_precision_score(y_test, p_test))

    gb = HistGradientBoostingClassifier(random_state=seed).fit(x_train, y_train)
    gb_pr_auc = float(average_precision_score(y_test, gb.predict_proba(x_test)[:, 1]))

    model = LogisticModel(
        version="v1",
        algo="logistic_regression",
        features=MODEL_FEATURES,
        means=tuple(float(v) for v in scaler.mean_),
        scales=tuple(float(v) for v in scaler.scale_),
        coefficients=tuple(float(v) for v in lr.coef_[0]),
        intercept=float(lr.intercept_[0]),
    )
    # Parity: the API's pure-Python scoring must match scikit-learn on held-out rows.
    pure = np.array([model.probability(r.features) for r in test_rows])
    parity = float(np.abs(pure - p_test).max())

    # Full decision pipeline (rules + this model + policy) on the held-out window.
    engine = RiskEngine(
        rules=load_rules(ROOT, rules_version), model=model, policy=load_policy(ROOT, policy_version)
    )
    outcomes = [engine.evaluate(r.features) for r in test_rows]
    decisions = np.array([o.decision.value for o in outcomes])
    hit_counts = Counter(code for o in outcomes for code in o.reason_codes)

    metrics: dict[str, Any] = {
        "dataset": "synthetic",
        "test_window_days": TEST_DAYS,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_fraud_rate": round(float(y_train.mean()), 5),
        "test_fraud_rate": round(float(y_test.mean()), 5),
        "model": {
            "pr_auc": round(lr_pr_auc, 4),
            "roc_auc": round(float(roc_auc_score(y_test, p_test)), 4),
            "at_0_5": _rates((p_test >= 0.5).astype(int), y_test),
        },
        "gradient_boosting_pr_auc": round(gb_pr_auc, 4),
        "gradient_boosting_adopted": gb_pr_auc - lr_pr_auc >= GB_ADOPTION_MARGIN,
        "policy": {
            "version": engine.policy_version,
            "decisions": dict(Counter(decisions.tolist())),
            "decline": _rates((decisions == "decline").astype(int), y_test),
            "review_or_decline": _rates((decisions != "approve").astype(int), y_test),
            "rule_hits": dict(sorted(hit_counts.items())),
        },
        "parity_max_abs_diff": parity,
    }
    training = {
        "seed": seed,
        "synthetic_config": {
            "customers": config.customers,
            "merchants": config.merchants,
            "days": config.days,
            "end": config.end.isoformat(),
        },
        "split": f"time-based; test = last {TEST_DAYS} days",
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "estimator": "StandardScaler + LogisticRegression(C=1.0, lbfgs, max_iter=2000)",
        "python": platform.python_version(),
        "scikit_learn": sklearn.__version__,
        "numpy": np.__version__,
    }
    artifact = model.model_copy(update={"metrics": metrics, "training": training})
    return artifact.model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the LR risk model on synthetic data.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--rules-version", default="1.0")
    parser.add_argument("--policy-version", default="1.0")
    parser.add_argument("--out", type=Path, default=ROOT / "models" / "lr-v1.json")
    args = parser.parse_args()

    artifact = train(args.seed, args.rules_version, args.policy_version)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(artifact["metrics"], indent=2, sort_keys=True) + "\n")
    sys.stdout.write(f"wrote {args.out}\n")


if __name__ == "__main__":
    main()
