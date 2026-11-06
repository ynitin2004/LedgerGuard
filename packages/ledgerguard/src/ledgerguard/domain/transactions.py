"""Transaction entity and its invariants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

# Postgres BIGINT upper bound: amounts are stored as amount_minor BIGINT.
MAX_AMOUNT_MINOR = 2**63 - 1

_CURRENCY = re.compile(r"[A-Z]{3}")
_MCC = re.compile(r"[0-9]{4}")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{1,64}")


class TransactionStatus(StrEnum):
    """Lifecycle (plan A5). ``decision`` is a separate concept, filled in M2."""

    CREATED = "created"
    EVALUATED = "evaluated"
    SETTLED_SIM = "settled_sim"
    FAILED_SIM = "failed_sim"


class Decision(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"
    REVIEW = "review"


class InvalidTransactionError(ValueError):
    """A transaction violates a domain invariant."""


@dataclass(frozen=True, slots=True)
class NewTransaction:
    """A payment as submitted by an API client, already normalised (currency upper-case)."""

    merchant_id: str
    merchant_category: str
    customer_token: str
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        # bool is an int subclass; True must not become a 1-cent payment.
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise InvalidTransactionError("amount_minor must be an integer")
        if not 0 < self.amount_minor <= MAX_AMOUNT_MINOR:
            raise InvalidTransactionError("amount_minor must be between 1 and 2^63-1")
        if not _CURRENCY.fullmatch(self.currency):
            raise InvalidTransactionError("currency must be 3 upper-case letters (ISO 4217)")
        if not _MCC.fullmatch(self.merchant_category):
            raise InvalidTransactionError("merchant_category must be a 4-digit MCC")
        for name in ("merchant_id", "customer_token"):
            if not _TOKEN.fullmatch(getattr(self, name)):
                raise InvalidTransactionError(f"{name} must match [A-Za-z0-9_-]{{1,64}}")

    def canonical_fields(self) -> dict[str, object]:
        """The fields that define request identity for idempotency hashing."""
        return {
            "merchant_id": self.merchant_id,
            "merchant_category": self.merchant_category,
            "customer_token": self.customer_token,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class Transaction:
    id: UUID
    client_id: UUID
    merchant_id: str
    merchant_category: str
    customer_token: str
    amount_minor: int
    currency: str
    status: TransactionStatus
    created_at: datetime
    correlation_id: str | None = None
    # Risk outcome and the versions that produced it: filled from M2.
    decision: Decision | None = None
    risk_score: Decimal | None = None
    policy_version: str | None = None
    rules_version: str | None = None
    model_version: str | None = None
