"""Feature definitions: windows, cold start, statistics (pure, no I/O)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from ledgerguard.risk.features import (
    FEATURE_NAMES,
    HistoryItem,
    PaymentAttempt,
    VelocityCounts,
    compute_features,
    velocity_from_history,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
ATTEMPT = PaymentAttempt(
    amount_minor=2500, currency="USD", merchant_id="m_1", merchant_category="5411"
)


def item(
    ago: timedelta,
    amount: int = 2000,
    merchant: str = "m_1",
    mcc: str = "5411",
    status: str = "settled_sim",
    currency: str = "USD",
) -> HistoryItem:
    return HistoryItem(NOW - ago, amount, currency, merchant, mcc, status)


def features(history: list[HistoryItem], attempt: PaymentAttempt = ATTEMPT) -> dict[str, float]:
    return compute_features(attempt, history, NOW, velocity_from_history(history, NOW))


def test_velocity_windows_are_half_open() -> None:
    history = [
        item(timedelta(minutes=5)),  # exactly 5 min ago: outside the 5 min window
        item(timedelta(minutes=4, seconds=59)),
        item(timedelta(minutes=59)),
        item(timedelta(hours=1)),  # exactly 1 h ago: outside
        item(timedelta(seconds=-1)),  # in the future (clock skew): never counted
    ]
    assert velocity_from_history(history, NOW) == VelocityCounts(last_5m=1, last_1h=3)


def test_cold_start_customer_has_neutral_features() -> None:
    f = features([])
    assert f["history_count_30d"] == 0
    assert f["amount_zscore"] == 0.0  # not enough history for statistics
    assert f["is_new_merchant"] == 0  # "new" needs a history to be new against
    assert f["category_share_30d"] == 0.0
    assert set(f) == set(FEATURE_NAMES)


def test_amount_zscore_needs_five_same_currency_payments() -> None:
    four = [item(timedelta(days=d), amount=1000 + d) for d in range(1, 5)]
    assert features(four)["amount_zscore"] == 0.0
    # Other currencies do not count towards the statistics.
    eur = [item(timedelta(days=10), amount=1000, currency="EUR")]
    assert features(four + eur)["amount_zscore"] == 0.0
    five = [*four, item(timedelta(days=6), amount=1010)]
    assert features(five)["amount_zscore"] > 3  # 2500 vs ~1000s


def test_identical_past_amounts_cap_the_zscore() -> None:
    same = [item(timedelta(days=d), amount=1000) for d in range(1, 8)]
    assert features(same)["amount_zscore"] == 10.0
    equal = PaymentAttempt(1000, "USD", "m_1", "5411")
    assert features(same, equal)["amount_zscore"] == 0.0


def test_merchant_category_and_failure_features() -> None:
    history = [
        item(timedelta(days=2), merchant="m_2", mcc="5812"),
        item(timedelta(days=1), merchant="m_3", mcc="5411"),
        item(timedelta(hours=23), status="failed_sim", merchant="m_2", mcc="5812"),
        item(timedelta(hours=25), status="failed_sim", merchant="m_2", mcc="5812"),  # > 24 h
        item(timedelta(days=31), merchant="m_1"),  # outside 30 days: ignored entirely
    ]
    f = features(history)
    assert f["history_count_30d"] == 4
    assert f["is_new_merchant"] == 1  # m_1 only appears outside the window
    assert f["category_share_30d"] == 0.25
    assert f["failed_24h"] == 1


@given(
    st.lists(st.integers(min_value=-120, max_value=7200), max_size=40),
)
def test_velocity_counts_are_consistent(seconds_ago: list[int]) -> None:
    history = [item(timedelta(seconds=s)) for s in seconds_ago]
    v = velocity_from_history(history, NOW)
    assert 0 <= v.last_5m <= v.last_1h <= len(history)
    assert v.last_5m == sum(1 for s in seconds_ago if 0 <= s < 300)


@given(st.integers(min_value=1, max_value=10**12), st.lists(st.integers(1, 10**7), max_size=30))
def test_features_are_finite_rounded_and_complete(amount: int, past: list[int]) -> None:
    history = [item(timedelta(hours=i + 1), amount=a) for i, a in enumerate(past)]
    f = features(history, PaymentAttempt(amount, "USD", "m_x", "5999"))
    assert set(f) == set(FEATURE_NAMES)
    assert -10.0 <= f["amount_zscore"] <= 10.0
    assert 0.0 <= f["category_share_30d"] <= 1.0
    for value in f.values():
        assert value == value  # not NaN
        if isinstance(value, float):
            assert round(value, 6) == value
