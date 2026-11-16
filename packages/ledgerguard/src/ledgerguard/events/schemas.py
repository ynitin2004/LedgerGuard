"""Versioned event schemas and the envelope carried in Kafka headers.

Compatibility rule: a schema version is immutable. Additive changes ship as a new
version next to the old one; consumers keep reading every version still in the
retention window. Unknown (type, version) pairs are poison and go to the DLQ.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

TOPIC_EVENTS = "txn.events"
TOPIC_RETRY = "txn.events.retry"
TOPIC_DLQ = "txn.events.dlq"


class TransactionEvaluatedV1(BaseModel):
    """Emitted in the same database transaction as every evaluated payment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_id: UUID
    client_id: UUID
    customer_token: str
    merchant_id: str
    merchant_category: str
    amount_minor: int
    currency: str
    status: str
    decision: str
    risk_score: str
    reason_codes: list[str]
    policy_version: str
    risk_decision_id: UUID
    created_at: datetime


SCHEMAS: dict[tuple[str, int], type[BaseModel]] = {
    ("transaction.evaluated", 1): TransactionEvaluatedV1,
}


class PoisonMessageError(Exception):
    """A message that can never be processed (bad headers, unknown schema, invalid body)."""


@dataclass(frozen=True, slots=True)
class Envelope:
    event_id: UUID
    event_type: str
    schema_version: int
    aggregate_id: str
    payload: dict[str, Any]
    correlation_id: str | None = None

    def headers(self) -> dict[str, str]:
        h = {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "schema_version": str(self.schema_version),
        }
        if self.correlation_id:
            h["correlation_id"] = self.correlation_id
        return h

    def value(self) -> bytes:
        return json.dumps(self.payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def parse(self) -> BaseModel:
        schema = SCHEMAS.get((self.event_type, self.schema_version))
        if schema is None:
            raise PoisonMessageError(f"unknown schema {self.event_type} v{self.schema_version}")
        try:
            return schema.model_validate(self.payload)
        except ValidationError as exc:
            raise PoisonMessageError(f"invalid {self.event_type} v{self.schema_version}") from exc

    @classmethod
    def from_message(
        cls, key: bytes | None, value: bytes | None, headers: dict[str, str]
    ) -> Envelope:
        try:
            return cls(
                event_id=UUID(headers["event_id"]),
                event_type=headers["event_type"],
                schema_version=int(headers["schema_version"]),
                aggregate_id=(key or b"").decode("utf-8"),
                payload=json.loads(value or b"null"),
                correlation_id=headers.get("correlation_id"),
            )
        except (KeyError, ValueError) as exc:  # missing header, bad UUID, bad JSON
            raise PoisonMessageError(f"malformed message: {exc!r}") from exc
