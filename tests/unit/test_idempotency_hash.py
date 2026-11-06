"""Property tests: canonical request hashing (docs/API_SPEC.md, idempotency semantics)."""

from __future__ import annotations

import json
import random

from hypothesis import given
from hypothesis import strategies as st

from apps.api.routers.transactions import CreateTransactionRequest
from ledgerguard.domain.idempotency import canonical_json, request_hash
from ledgerguard.domain.transactions import MAX_AMOUNT_MINOR, NewTransaction

json_scalars = st.none() | st.booleans() | st.integers() | st.text(max_size=20)
json_objects = st.dictionaries(st.text(max_size=10), json_scalars, max_size=8)

tokens = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-",
    min_size=1,
    max_size=64,
)
requests: st.SearchStrategy[dict[str, object]] = st.builds(
    dict,
    merchant_id=tokens,
    merchant_category=st.from_regex(r"\A[0-9]{4}\Z"),
    customer_token=tokens,
    amount_minor=st.integers(min_value=1, max_value=MAX_AMOUNT_MINOR),
    currency=st.from_regex(r"\A[A-Za-z]{3}\Z"),
)


def _hash(payload: dict[str, object]) -> str:
    return request_hash(
        CreateTransactionRequest.model_validate(payload).to_domain().canonical_fields()
    )


@given(json_objects, st.randoms())
def test_canonical_json_ignores_key_order(obj: dict[str, object], rnd: random.Random) -> None:
    items = list(obj.items())
    rnd.shuffle(items)
    assert canonical_json(dict(items)) == canonical_json(obj)
    assert request_hash(dict(items)) == request_hash(obj)


@given(json_objects)
def test_canonical_json_is_compact_and_round_trips(obj: dict[str, object]) -> None:
    text = canonical_json(obj)
    assert json.loads(text) == obj
    assert ", " not in text.replace('", "', "")  # no separator whitespace outside strings


@given(requests)
def test_currency_case_and_field_order_do_not_change_the_hash(payload: dict[str, object]) -> None:
    lower = {**payload, "currency": str(payload["currency"]).lower()}
    upper = dict(reversed(list({**payload, "currency": str(payload["currency"]).upper()}.items())))
    assert _hash(lower) == _hash(upper)


@given(requests, st.integers(min_value=1, max_value=MAX_AMOUNT_MINOR))
def test_any_amount_change_changes_the_hash(payload: dict[str, object], other: int) -> None:
    changed = {**payload, "amount_minor": other}
    assert (_hash(changed) == _hash(payload)) == (other == payload["amount_minor"])


@given(requests)
def test_hash_is_sha256_hex(payload: dict[str, object]) -> None:
    digest = _hash(payload)
    assert len(digest) == 64
    int(digest, 16)


def test_hash_covers_exactly_the_request_fields() -> None:
    new = NewTransaction("m", "5411", "c", 1, "USD")
    assert set(new.canonical_fields()) == {
        "merchant_id",
        "merchant_category",
        "customer_token",
        "amount_minor",
        "currency",
    }
