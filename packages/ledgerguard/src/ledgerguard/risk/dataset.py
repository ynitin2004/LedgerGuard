"""Turn a chronological transaction stream into (features, label) rows.

Each row's features come from ``compute_features`` with only the customer's strictly
earlier transactions as history, and velocity from ``velocity_from_history``: exactly
what the hot path computes, so training and serving see the same feature definitions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ledgerguard.risk.features import (
    HISTORY_WINDOW,
    FeatureSnapshot,
    HistoryItem,
    PaymentAttempt,
    compute_features,
    velocity_from_history,
)
from ledgerguard.synthetic import SyntheticTxn


@dataclass(frozen=True, slots=True)
class LabelledRow:
    features: FeatureSnapshot
    is_fraud: bool
    created_at: datetime
    customer_token: str


def build_rows(txns: Iterable[SyntheticTxn]) -> list[LabelledRow]:
    histories: dict[str, list[HistoryItem]] = {}
    rows: list[LabelledRow] = []
    for t in sorted(txns, key=lambda x: x.created_at):
        history = histories.setdefault(t.customer_token, [])
        # Drop items older than the window (history is append-only and time-ordered).
        while history and history[0].created_at <= t.created_at - HISTORY_WINDOW:
            history.pop(0)
        prior = [h for h in history if h.created_at < t.created_at]
        attempt = PaymentAttempt(t.amount_minor, t.currency, t.merchant_id, t.merchant_category)
        features = compute_features(
            attempt, prior, t.created_at, velocity_from_history(prior, t.created_at)
        )
        rows.append(LabelledRow(features, t.is_fraud, t.created_at, t.customer_token))
        history.append(
            HistoryItem(
                t.created_at,
                t.amount_minor,
                t.currency,
                t.merchant_id,
                t.merchant_category,
                t.status,
            )
        )
    return rows
