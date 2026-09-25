"""Transport-level request body cap.

Starlette parses a multipart body before an endpoint can see it, spooling
file parts to disk as they arrive — so by the time application code measures
a payload, a lying client has already made the server store all of it. This
middleware is the earlier line: a body that *declares* more than the cap is
refused outright, and one that arrives without an honest Content-Length
(chunked, or lying) is cut off mid-stream the moment the counted bytes pass
the cap. Endpoints still enforce their own exact limits; this is the ceiling
on what any request may make the process ingest at all.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(HTTPException):
    """Raised from inside ``receive``.

    An HTTPException on purpose: FastAPI re-raises middleware HTTPExceptions
    unwrapped while it parses the body, so this surfaces to the client as a
    clean 413 even when the cap trips halfway through multipart parsing.
    """

    def __init__(self, max_bytes: int) -> None:
        super().__init__(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Request body exceeds the {max_bytes // (1024 * 1024)} MB limit",
        )


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _send_413(send, self.max_bytes)
            return

        received = 0
        response_started = False

        async def guarded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge(self.max_bytes)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, tracking_send)
        except _BodyTooLarge:
            # Normally the exception is rendered by the app's own exception
            # middleware and never reaches here; this catch covers a body
            # drained outside that scope. Once a response has started there
            # is nothing left to send — returning lets the server close.
            if not response_started:
                await _send_413(send, self.max_bytes)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_413(send: Send, max_bytes: int) -> None:
    body = json.dumps(
        {"detail": f"Request body exceeds the {max_bytes // (1024 * 1024)} MB limit"}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status.HTTP_413_CONTENT_TOO_LARGE,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
