"""Input validation at the edge (Pydantic) and invariants in the domain."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from apps.api.routers.transactions import CreateTransactionRequest
from ledgerguard.domain.api_keys import API_KEY_PREFIX, generate_api_key, hash_api_key
from ledgerguard.domain.transactions import (
    MAX_AMOUNT_MINOR,
    InvalidTransactionError,
    NewTransaction,
)

VALID: dict[str, Any] = {
    "merchant_id": "m_1",
    "merchant_category": "5411",
    "customer_token": "cus_1",
    "amount_minor": 100,
    "currency": "usd",
}


def test_valid_request_is_normalised() -> None:
    req = CreateTransactionRequest.model_validate(VALID)
    assert req.currency == "USD"
    assert req.to_domain() == NewTransaction("m_1", "5411", "cus_1", 100, "USD")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount_minor", 12.5),  # money is never a float
        ("amount_minor", 100.0),
        ("amount_minor", "100"),
        ("amount_minor", True),
        ("amount_minor", 0),
        ("amount_minor", -1),
        ("amount_minor", MAX_AMOUNT_MINOR + 1),
        ("currency", "US"),
        ("currency", "USDX"),
        ("currency", "U$D"),
        ("currency", "ÜSD"),
        ("merchant_category", "541"),
        ("merchant_category", "54a1"),
        ("customer_token", ""),
        ("customer_token", "has space"),
        ("customer_token", "x" * 65),
        ("merchant_id", "m/1"),
    ],
)
def test_invalid_field_is_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as info:
        CreateTransactionRequest.model_validate({**VALID, field: value})
    assert info.value.errors()[0]["loc"] == (field,)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateTransactionRequest.model_validate({**VALID, "decision": "approve"})


@given(st.integers().filter(lambda n: not 0 < n <= MAX_AMOUNT_MINOR))
def test_domain_rejects_out_of_range_amounts(amount: int) -> None:
    with pytest.raises(InvalidTransactionError):
        NewTransaction("m", "5411", "c", amount, "USD")


@given(st.integers(min_value=1, max_value=MAX_AMOUNT_MINOR))
def test_domain_accepts_every_bigint_positive_amount(amount: int) -> None:
    assert NewTransaction("m", "5411", "c", amount, "USD").amount_minor == amount


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount_minor": True},
        {"currency": "usd"},  # the domain requires normalised input
        {"merchant_category": "12345"},
        {"customer_token": "a b"},
    ],
)
def test_domain_invariants(kwargs: dict[str, Any]) -> None:
    values: dict[str, Any] = {**VALID, "currency": "USD", **kwargs}
    with pytest.raises(InvalidTransactionError):
        NewTransaction(**values)


def test_api_keys_are_random_prefixed_and_hashed() -> None:
    a, b = generate_api_key(), generate_api_key()
    assert a != b
    assert a.startswith(API_KEY_PREFIX)
    assert len(a) > 40
    assert hash_api_key(a) != a
    assert hash_api_key(a) == hash_api_key(a)
    assert len(hash_api_key(a)) == 64
