"""Feature computation, shared by the hot path, training and replay.

Inputs are the customer's recent history (from Postgres) and velocity counts (from
Redis windows, or derived from the same history when Redis is unavailable). The
output is a flat ``FeatureSnapshot`` that is stored with every decision, so any
decision can be recomputed from its snapshot alone.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

FeatureSnapshot = dict[str, float]

FEATURE_NAMES: tuple[str, ...] = (
    "amount_minor",
    "log_amount",
    "velocity_5m",
    "velocity_1h",
    "history_count_30d",
    "amount_zscore",
    "is_new_merchant",
    "category_share_30d",
    "failed_24h",
)

HISTORY_WINDOW = timedelta(days=30)
VELOCITY_5M = timedelta(minutes=5)
VELOCITY_1H = timedelta(hours=1)
FAILURE_WINDOW = timedelta(hours=24)
# Below this many same-currency payments, amount statistics are not meaningful
# (cold start, plan A4): the z-score is reported as 0.
MIN_HISTORY_FOR_STATS = 5
# Fixed precision keeps snapshots readable and makes stored values replay exactly.
PRECISION = 6


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """One earlier transaction of the same customer (same API client)."""

    created_at: datetime
    amount_minor: int
    currency: str
    merchant_id: str
    merchant_category: str
    status: str
    # Identity only (rebuilding Redis windows); never used as a feature.
    transaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentAttempt:
    """The fields of the payment being evaluated that features depend on."""

    amount_minor: int
    currency: str
    merchant_id: str
    merchant_category: str


@dataclass(frozen=True, slots=True)
class VelocityCounts:
    last_5m: int
    last_1h: int


def velocity_from_history(history: Sequence[HistoryItem], now: datetime) -> VelocityCounts:
    """Window counts from history: the Postgres fallback and the offline definition."""
    return VelocityCounts(
        last_5m=sum(1 for h in history if now - VELOCITY_5M < h.created_at <= now),
        last_1h=sum(1 for h in history if now - VELOCITY_1H < h.created_at <= now),
    )


def _zscore(value: float, sample: Sequence[float]) -> float:
    if len(sample) < MIN_HISTORY_FOR_STATS:
        return 0.0
    mean = math.fsum(sample) / len(sample)
    variance = math.fsum((x - mean) ** 2 for x in sample) / len(sample)
    std = math.sqrt(variance)
    if std < 1e-9:
        # Identical past amounts: any different amount is maximally unusual, but an
        # unbounded z-score would dominate the model. Cap it instead.
        return 0.0 if math.isclose(value, mean) else 10.0
    return (value - mean) / std


def compute_features(
    attempt: PaymentAttempt,
    history: Sequence[HistoryItem],
    now: datetime,
    velocity: VelocityCounts,
) -> FeatureSnapshot:
    recent = [h for h in history if now - HISTORY_WINDOW < h.created_at <= now]
    log_amount = math.log1p(attempt.amount_minor)
    same_currency = [math.log1p(h.amount_minor) for h in recent if h.currency == attempt.currency]
    known_merchants = {h.merchant_id for h in recent}
    in_category = sum(1 for h in recent if h.merchant_category == attempt.merchant_category)
    failed_24h = sum(
        1 for h in recent if h.status == "failed_sim" and now - FAILURE_WINDOW < h.created_at
    )
    snapshot: FeatureSnapshot = {
        "amount_minor": attempt.amount_minor,
        "log_amount": log_amount,
        "velocity_5m": velocity.last_5m,
        "velocity_1h": velocity.last_1h,
        "history_count_30d": len(recent),
        "amount_zscore": min(max(_zscore(log_amount, same_currency), -10.0), 10.0),
        "is_new_merchant": 1 if recent and attempt.merchant_id not in known_merchants else 0,
        "category_share_30d": in_category / len(recent) if recent else 0.0,
        "failed_24h": failed_24h,
    }
    return {k: round(v, PRECISION) if isinstance(v, float) else v for k, v in snapshot.items()}
