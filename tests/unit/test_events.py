"""Event envelopes, schema registry and retry/DLQ routing (pure)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledgerguard.events.retry import MAX_ATTEMPTS, MAX_DELAY_S, backoff_s, route_failure
from ledgerguard.events.schemas import Envelope, PoisonMessageError, TransactionEvaluatedV1


def payload(**overrides: Any) -> dict[str, Any]:
    body = TransactionEvaluatedV1(
        transaction_id=uuid4(),
        client_id=uuid4(),
        customer_token="cus_1",
        merchant_id="m_1",
        merchant_category="5411",
        amount_minor=1250,
        currency="USD",
        status="evaluated",
        decision="approve",
        risk_score="0.01000",
        reason_codes=[],
        policy_version="rules@1.0+model@v1+thresholds@1.0",
        risk_decision_id=uuid4(),
        created_at=datetime(2026, 11, 16, tzinfo=UTC),
    ).model_dump(mode="json")
    body.update(overrides)
    return body


def envelope(**overrides: Any) -> Envelope:
    fields: dict[str, Any] = {
        "event_id": uuid4(),
        "event_type": "transaction.evaluated",
        "schema_version": 1,
        "aggregate_id": "txn-1",
        "payload": payload(),
        "correlation_id": "corr-1",
    }
    fields.update(overrides)
    return Envelope(**fields)


def test_envelope_round_trips_through_a_kafka_message() -> None:
    original = envelope()
    copy = Envelope.from_message(
        original.aggregate_id.encode(), original.value(), original.headers()
    )
    assert copy == original
    parsed = copy.parse()
    assert isinstance(parsed, TransactionEvaluatedV1)
    assert parsed.amount_minor == 1250


@pytest.mark.parametrize(
    ("headers", "value"),
    [
        ({"event_type": "transaction.evaluated", "schema_version": "1"}, b"{}"),  # no event_id
        ({"event_id": "nope", "event_type": "x", "schema_version": "1"}, b"{}"),
        ({"event_id": str(uuid4()), "event_type": "x", "schema_version": "one"}, b"{}"),
        ({"event_id": str(uuid4()), "event_type": "x", "schema_version": "1"}, b"{not json"),
    ],
)
def test_malformed_messages_are_poison(headers: dict[str, str], value: bytes) -> None:
    with pytest.raises(PoisonMessageError):
        Envelope.from_message(b"k", value, headers)


def test_unknown_schema_or_invalid_body_is_poison() -> None:
    with pytest.raises(PoisonMessageError, match="unknown schema"):
        envelope(schema_version=99).parse()
    with pytest.raises(PoisonMessageError, match="invalid"):
        envelope(payload=payload(amount_minor="lots")).parse()
    with pytest.raises(PoisonMessageError):
        envelope(payload=payload(surprise=True)).parse()  # schemas forbid unknown fields


def test_value_is_canonical_json() -> None:
    value = envelope().value()
    assert json.loads(value) == envelope().payload | {
        k: json.loads(value)[k] for k in ("transaction_id", "client_id", "risk_decision_id")
    }
    assert b" " not in value.replace(b'" "', b"")


def test_poison_goes_straight_to_the_dlq() -> None:
    route = route_failure(1, PoisonMessageError("bad"))
    assert route.destination == "dlq"
    assert route.reason.startswith("poison")


def test_transient_errors_retry_until_the_limit() -> None:
    for attempt in range(1, MAX_ATTEMPTS):
        route = route_failure(attempt, ConnectionError("db down"), rand=lambda: 1.0)
        assert route.destination == "retry"
        assert route.next_attempt == attempt + 1
        assert route.delay_s == min(MAX_DELAY_S, 2 ** (attempt - 1))
    last = route_failure(MAX_ATTEMPTS, ConnectionError("db down"))
    assert last.destination == "dlq"
    assert "exhausted" in last.reason


@given(st.integers(min_value=1, max_value=50), st.floats(min_value=0, max_value=0.999999))
def test_backoff_is_bounded_full_jitter(attempt: int, r: float) -> None:
    delay = backoff_s(attempt, rand=lambda: r)
    assert 0 <= delay < MAX_DELAY_S
    assert delay <= 2 ** (attempt - 1)
