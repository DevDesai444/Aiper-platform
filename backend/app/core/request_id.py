"""Per-request correlation id.

Generated (or adopted from an `X-Request-ID` the caller sent) at the ASGI
boundary and held in a contextvar for the rest of that request's async call
chain — including the agent turn a chat request triggers deep inside
`app.agents.runtime`, and the identity `app.core.deps.current_user` learns
partway through. Neither of those call sites has to accept a new parameter
just to pass an id along; they read it back with `get_request_id()`.

Contextvars are inherited by child tasks created *after* they are set, which
covers this app's one streaming route: `StreamingResponse` iterates its body
in a child task spawned while this middleware's `__call__` is still on the
stack, so the id set here is still live for every log line the stream emits.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("aiper.access")

_MAX_CLIENT_ID_LEN = 128
_HEADER_NAME = b"x-request-id"

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aiper_request_id", default=None
)
_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aiper_request_user_id", default=None
)
_org_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aiper_request_org_id", default=None
)


def get_request_id() -> str | None:
    return _request_id.get()


def get_request_user_id() -> str | None:
    return _user_id.get()


def get_request_org_id() -> str | None:
    return _org_id.get()


def bind_request_identity(*, user_id: object, org_id: object) -> None:
    """Publish the authenticated identity for the rest of this request's logs.

    Called once identity is resolved (`app.core.deps.current_user`). A no-op
    cost for routes that never authenticate — those simply never call it, and
    `get_request_user_id()`/`get_request_org_id()` stay `None`.
    """
    _user_id.set(str(user_id) if user_id is not None else None)
    _org_id.set(str(org_id) if org_id is not None else None)


def _client_supplied_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == _HEADER_NAME:
            candidate = value.decode("latin-1", errors="replace")[:_MAX_CLIENT_ID_LEN].strip()
            return candidate or None
    return None


class RequestIDMiddleware:
    """Outermost application middleware: assigns/propagates the request id,
    exposes it on the response, and logs one JSON access line per request.

    Registered last in `app.main` (Starlette wraps in reverse `add_middleware`
    order, so the last one added is outermost) so the id is live before CORS,
    the body-size cap, or routing run, and the access log's status code is
    whatever the client actually received — including a CORS rejection or a
    413 from the body-limit middleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _client_supplied_id(scope) or str(uuid.uuid4())
        id_token = _request_id.set(request_id)
        user_token = _user_id.set(None)
        org_token = _org_id.set(None)
        started = time.monotonic()
        status_code = 500

        async def send_with_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((_HEADER_NAME, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            logger.info(
                "http_request",
                extra={
                    "json_fields": {
                        "event": "http_request",
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status_code": status_code,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    }
                },
            )
            _request_id.reset(id_token)
            _user_id.reset(user_token)
            _org_id.reset(org_token)
