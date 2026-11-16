"""Seeded synthetic payment history with labelled fraud (plan A4).

All data is synthetic. Customers have habits (home categories, favourite merchants,
typical amounts, a daily rhythm); a small share of them suffer one fraud incident
following one of three patterns:

- ``card_testing``: a burst of small payments at new merchants, many failing;
- ``account_takeover``: a few very large payments at new merchants in risky categories;
- ``fail_then_success``: repeated failures at one merchant, then a larger success.

Legitimate noise is deliberate (new merchants, occasional big purchases, failures), so
no single feature separates the classes. The same ``seed`` always yields the same data.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

COMMON_MCCS = ("5411", "5812", "5541", "5999", "4111", "5691", "5912", "5814")
RISKY_MCCS = ("5732", "5944", "7995", "6051", "4829")
CURRENCIES = (("USD", 0.7), ("EUR", 0.2), ("GBP", 0.1))
PATTERNS = (("card_testing", 0.4), ("account_takeover", 0.4), ("fail_then_success", 0.2))


@dataclass(frozen=True, slots=True)
class SyntheticTxn:
    customer_token: str
    merchant_id: str
    merchant_category: str
    amount_minor: int
    currency: str
    created_at: datetime
    status: str  # settled_sim | failed_sim
    is_fraud: bool
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class SyntheticConfig:
    seed: int = 7
    customers: int = 2000
    merchants: int = 400
    days: int = 30
    end: datetime = field(default_factory=lambda: datetime(2026, 1, 31, tzinfo=UTC))
    compromised_share: float = 0.04
    new_customer_share: float = 0.1
    legit_failure_rate: float = 0.03


@dataclass(frozen=True, slots=True)
class _Merchant:
    merchant_id: str
    mcc: str


@dataclass(frozen=True, slots=True)
class _Customer:
    token: str
    currency: str
    pool: list[_Merchant]
    favourites: list[_Merchant]
    median: float
    sigma: float


def _legit(
    rng: random.Random,
    c: _Customer,
    at: datetime,
    merchants: list[_Merchant],
    cfg: SyntheticConfig,
) -> SyntheticTxn:
    r = rng.random()
    if r < 0.85:
        merchant = rng.choice(c.favourites)
    elif r < 0.97:
        merchant = rng.choice(c.pool)  # new merchant, familiar category
    else:
        merchant = rng.choice(merchants)  # anything, including risky categories
    amount = _lognormal_amount(rng, c.median, c.sigma)
    if rng.random() < 0.02:
        amount *= rng.randint(4, 12)  # occasional big legitimate purchase
    status = "failed_sim" if rng.random() < cfg.legit_failure_rate else "settled_sim"
    return SyntheticTxn(
        c.token, merchant.merchant_id, merchant.mcc, amount, c.currency, at, status, False
    )


def _weighted(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def _lognormal_amount(rng: random.Random, median: float, sigma: float) -> int:
    return max(50, int(math.exp(rng.gauss(math.log(median), sigma))))


def generate(config: SyntheticConfig | None = None) -> list[SyntheticTxn]:
    """Return every synthetic transaction, ordered by time."""
    cfg = config or SyntheticConfig()
    rng = random.Random(cfg.seed)  # noqa: S311 - simulation, not cryptography
    start = cfg.end - timedelta(days=cfg.days)

    merchants = [
        _Merchant(
            f"m_{i:05d}", RISKY_MCCS[i % len(RISKY_MCCS)] if i % 8 == 0 else rng.choice(COMMON_MCCS)
        )
        for i in range(cfg.merchants)
    ]
    by_mcc: dict[str, list[_Merchant]] = {}
    for m in merchants:
        by_mcc.setdefault(m.mcc, []).append(m)
    risky = [m for m in merchants if m.mcc in RISKY_MCCS]

    out: list[SyntheticTxn] = []
    for c in range(cfg.customers):
        token = f"cus_{c:05d}"
        currency = _weighted(rng, CURRENCIES)
        home = rng.sample(COMMON_MCCS, k=rng.randint(2, 4))
        pool = [m for mcc in home for m in by_mcc.get(mcc, [])]
        favourites = rng.sample(pool, k=min(len(pool), rng.randint(4, 12)))
        median = rng.uniform(1500, 6000)
        sigma = rng.uniform(0.4, 0.8)
        per_day = rng.uniform(0.5, 3.0)
        joined = start
        if rng.random() < cfg.new_customer_share:
            joined = cfg.end - timedelta(days=rng.uniform(1, 6))

        profile = _Customer(token, currency, pool, favourites, median, sigma)

        # Legitimate activity: Poisson arrivals, more during the day.
        at = joined
        while True:
            at += timedelta(hours=rng.expovariate(per_day / 24))
            if at >= cfg.end:
                break
            if 1 <= at.hour < 6 and rng.random() < 0.7:
                continue  # quiet night
            out.append(_legit(rng, profile, at, merchants, cfg))
            if rng.random() < 0.06:
                # Legitimate bursts (a shopping trip, a split bill), so velocity alone
                # does not separate fraud from normal behaviour.
                burst_at = at
                for _ in range(rng.randint(1, 4)):
                    burst_at += timedelta(seconds=rng.uniform(20, 240))
                    if burst_at < cfg.end:
                        out.append(_legit(rng, profile, burst_at, merchants, cfg))

        if rng.random() < cfg.compromised_share:
            incident_start = max(joined, start + timedelta(days=cfg.days / 3))
            incident = incident_start + timedelta(
                seconds=rng.uniform(0, max(60.0, (cfg.end - incident_start).total_seconds() - 7200))
            )
            out.extend(_incident(rng, token, currency, incident, merchants, risky, median))

    out.sort(key=lambda t: (t.created_at, t.customer_token))
    return out


def _incident(
    rng: random.Random,
    token: str,
    currency: str,
    at: datetime,
    merchants: list[_Merchant],
    risky: list[_Merchant],
    median: float,
) -> list[SyntheticTxn]:
    pattern = _weighted(rng, PATTERNS)
    txns: list[SyntheticTxn] = []

    def add(merchant: _Merchant, amount: int, status: str, offset_s: float) -> None:
        txns.append(
            SyntheticTxn(
                token,
                merchant.merchant_id,
                merchant.mcc,
                amount,
                currency,
                at + timedelta(seconds=offset_s),
                status,
                True,
                pattern,
            )
        )

    if pattern == "card_testing":
        targets = rng.sample(merchants, k=rng.randint(1, 3))
        t = 0.0
        for _ in range(rng.randint(5, 14)):
            t += rng.uniform(10, 60)
            status = "failed_sim" if rng.random() < 0.5 else "settled_sim"
            add(rng.choice(targets), rng.randint(100, 600), status, t)
    elif pattern == "account_takeover":
        t = 0.0
        for _ in range(rng.randint(1, 4)):
            t += rng.uniform(120, 1800)
            amount = int(median * rng.uniform(8, 30))
            add(rng.choice(risky), amount, "settled_sim", t)
    else:  # fail_then_success
        target = rng.choice(merchants)
        t = 0.0
        for _ in range(rng.randint(3, 6)):
            t += rng.uniform(30, 300)
            add(target, int(median * rng.uniform(1, 4)), "failed_sim", t)
        add(target, int(median * rng.uniform(2, 6)), "settled_sim", t + rng.uniform(30, 300))
    return txns
