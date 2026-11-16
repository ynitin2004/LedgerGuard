"""Synthetic generator and dataset builder: determinism and leakage-free features."""

from __future__ import annotations

from datetime import UTC, datetime

from ledgerguard.risk.dataset import build_rows
from ledgerguard.synthetic import SyntheticConfig, generate

SMALL = SyntheticConfig(seed=3, customers=150, merchants=60, compromised_share=0.2)


def test_same_seed_same_data() -> None:
    assert generate(SMALL) == generate(SMALL)
    assert generate(SMALL) != generate(SyntheticConfig(seed=4, customers=150, merchants=60))


def test_data_shape() -> None:
    txns = generate(SMALL)
    assert txns == sorted(txns, key=lambda t: (t.created_at, t.customer_token))
    assert all(t.amount_minor > 0 for t in txns)
    assert all(t.created_at < SMALL.end for t in txns)
    assert {t.status for t in txns} <= {"settled_sim", "failed_sim"}
    fraud = [t for t in txns if t.is_fraud]
    assert fraud
    assert all(t.pattern for t in fraud)
    assert not any(t.pattern for t in txns if not t.is_fraud)


def test_features_only_use_strictly_earlier_history() -> None:
    txns = generate(SMALL)
    rows = build_rows(txns)
    assert len(rows) == len(txns)
    seen: set[str] = set()
    for row in rows:
        if row.customer_token not in seen:
            # A customer's first payment has no history and no velocity.
            assert row.features["history_count_30d"] == 0
            assert row.features["velocity_1h"] == 0
            seen.add(row.customer_token)


def test_end_date_controls_the_window() -> None:
    end = datetime(2030, 5, 1, tzinfo=UTC)
    txns = generate(SyntheticConfig(seed=3, customers=20, merchants=30, end=end))
    assert max(t.created_at for t in txns) < end
