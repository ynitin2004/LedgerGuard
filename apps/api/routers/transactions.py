"""``/v1/transactions``: HTTP <-> use case translation only (docs/API_SPEC.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.deps import get_create_transaction, get_current_client, get_get_transaction
from apps.api.problems import Problem
from ledgerguard.application.ports import ApiClient
from ledgerguard.application.transactions import CreateTransaction, GetTransaction
from ledgerguard.domain.transactions import MAX_AMOUNT_MINOR, NewTransaction

router = APIRouter(prefix="/v1/transactions", tags=["transactions"])

_TOKEN = r"^[A-Za-z0-9_\-]{1,64}$"  # noqa: S105 - a regex, not a secret
# Printable ASCII without spaces; long enough for UUIDs and composite client keys.
IDEMPOTENCY_KEY_PATTERN = r"^[\x21-\x7E]{1,255}$"


class CreateTransactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_id: str = Field(pattern=_TOKEN)
    merchant_category: str = Field(pattern=r"^[0-9]{4}$", description="ISO 18245 MCC")
    customer_token: str = Field(pattern=_TOKEN, description="Opaque token, never a PAN")
    # strict: money is never accepted as a float (or a numeric string).
    amount_minor: int = Field(strict=True, gt=0, le=MAX_AMOUNT_MINOR)
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217, any case")

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        if not value.isascii() or not value.isalpha():
            raise ValueError("currency must be 3 letters")
        return value.upper()

    def to_domain(self) -> NewTransaction:
        return NewTransaction(
            merchant_id=self.merchant_id,
            merchant_category=self.merchant_category,
            customer_token=self.customer_token,
            amount_minor=self.amount_minor,
            currency=self.currency,
        )


class TransactionResponse(BaseModel):
    id: UUID
    status: Literal["created", "evaluated", "settled_sim", "failed_sim"]
    merchant_id: str
    merchant_category: str
    customer_token: str
    amount_minor: int
    currency: str
    decision: Literal["approve", "decline", "review"] | None
    risk_score: str | None
    policy_version: str | None
    created_at: datetime


_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem, "description": "Missing or invalid API key"},
    422: {"model": Problem, "description": "Validation failed"},
}


@router.post(
    "",
    status_code=201,
    response_model=TransactionResponse,
    responses={
        **_ERRORS,
        409: {"model": Problem, "description": "Idempotency-Key reused with a different body"},
    },
)
async def create_transaction(
    body: CreateTransactionRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            pattern=IDEMPOTENCY_KEY_PATTERN,
            description="Client-chosen unique key; retries with the same key and body "
            "return the original response for 24 h.",
        ),
    ],
    client: Annotated[ApiClient, Depends(get_current_client)],
    create: Annotated[CreateTransaction, Depends(get_create_transaction)],
) -> JSONResponse:
    result = await create(
        client=client,
        idempotency_key=idempotency_key,
        new=body.to_domain(),
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    headers = {"Idempotent-Replayed": "true"} if result.replayed else None
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)


@router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    responses={**_ERRORS, 404: {"model": Problem, "description": "Not found for this client"}},
)
async def get_transaction(
    transaction_id: UUID,
    client: Annotated[ApiClient, Depends(get_current_client)],
    get: Annotated[GetTransaction, Depends(get_get_transaction)],
) -> JSONResponse:
    return JSONResponse(await get(client=client, transaction_id=transaction_id))
