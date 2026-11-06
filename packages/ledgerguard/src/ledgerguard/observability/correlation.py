"""Correlation-ID middleware (pure ASGI, so no BaseHTTPMiddleware overhead).

A caller-supplied ``X-Correlation-ID`` is accepted only if it is short and made of
safe characters; otherwise a UUID4 is generated. The ID is bound into structlog
context variables, attached to the active trace span, and echoed in the response.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

import structlog
from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER_NAME = "x-correlation-id"
_VALID = re.compile(r"[A-Za-z0-9._\-]{1,128}")

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def resolve_correlation_id(raw: str | None) -> str:
    if raw and _VALID.fullmatch(raw):
        return raw
    return str(uuid.uuid4())


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw: str | None = None
        for name, value in scope["headers"]:
            if name.decode("latin-1").lower() == HEADER_NAME:
                raw = value.decode("latin-1")
                break
        cid = resolve_correlation_id(raw)

        # Also exposed as request.state.correlation_id: the scope dict is shared with the
        # outer error middleware, which runs after the context variable is reset.
        scope.setdefault("state", {})["correlation_id"] = cid
        token = correlation_id_var.set(cid)
        structlog.contextvars.bind_contextvars(correlation_id=cid)
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("correlation_id", cid)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Replace rather than append: error responses may already carry it.
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.decode("latin-1").lower() != HEADER_NAME
                ]
                headers.append((HEADER_NAME.encode("latin-1"), cid.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
            correlation_id_var.reset(token)
