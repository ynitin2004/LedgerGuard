"""RFC 9457 problem details for every error response.

Each error body is ``application/problem+json`` with ``type``, ``title``, ``status``,
``detail``, ``instance`` and ``correlation_id``, so a client can quote one ID to support.
Problem ``type`` values are URNs: stable identifiers that do not pretend to be
dereferenceable URLs.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from ledgerguard.application.transactions import (
    IdempotencyKeyReusedError,
    TransactionNotFoundError,
)
from ledgerguard.domain.transactions import InvalidTransactionError
from ledgerguard.observability.correlation import HEADER_NAME
from ledgerguard.observability.logging import get_logger

log = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"
URN = "urn:ledgerguard:problem:"


class Problem(BaseModel):
    """Schema of error responses (documented in OpenAPI)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    correlation_id: str | None = None
    errors: list[dict[str, Any]] | None = None


class ProblemError(Exception):
    """Raise from routers/dependencies to return a specific problem."""

    def __init__(
        self,
        status: int,
        problem_type: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.problem_type = problem_type
        self.title = title
        self.detail = detail
        self.headers = headers


def problem_response(
    request: Request,
    *,
    status: int,
    title: str | None = None,
    problem_type: str = "about:blank",
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    correlation_id: str | None = getattr(request.state, "correlation_id", None)
    body = Problem(
        type=problem_type,
        title=title or HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        errors=errors,
    )
    all_headers = dict(headers or {})
    if correlation_id:
        # The correlation middleware cannot add it to responses produced by the
        # outermost error handler (unhandled exceptions), so set it explicitly.
        all_headers[HEADER_NAME] = correlation_id
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=all_headers,
    )


async def _problem_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemError)  # noqa: S101 - registered for this type only
    return problem_response(
        request,
        status=exc.status,
        title=exc.title,
        problem_type=exc.problem_type,
        detail=exc.detail,
        headers=exc.headers,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    detail = exc.detail if isinstance(exc.detail, str) else None
    return problem_response(request, status=exc.status_code, detail=detail, headers=exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Echo locations and messages only; never the submitted values ("input").
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        problem_type=URN + "validation-error",
        title="Request validation failed",
        errors=errors,
    )


async def _idempotency_conflict(request: Request, exc: Exception) -> JSONResponse:
    return problem_response(
        request,
        status=409,
        problem_type=URN + "idempotency-key-reused",
        title="Idempotency key reused with a different request",
        detail="This Idempotency-Key was already used with a different body. "
        "Use a new key for a new request.",
    )


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    return problem_response(
        request,
        status=404,
        problem_type=URN + "transaction-not-found",
        title="Transaction not found",
    )


async def _invalid_transaction(request: Request, exc: Exception) -> JSONResponse:
    # Defence in depth: request models already enforce the same rules.
    return problem_response(
        request,
        status=422,
        problem_type=URN + "validation-error",
        title="Request validation failed",
        detail=str(exc),
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("api.unhandled_error", path=request.url.path)
    # No exception text in the response: it may contain internals.
    return problem_response(request, status=500)


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _problem_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(IdempotencyKeyReusedError, _idempotency_conflict)
    app.add_exception_handler(TransactionNotFoundError, _not_found)
    app.add_exception_handler(InvalidTransactionError, _invalid_transaction)
    app.add_exception_handler(Exception, _unhandled)
