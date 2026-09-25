"""Structured JSON logging for the whole process.

Every record — ours and uvicorn's — is rendered as one JSON line carrying the
current request's correlation id and (once known) the acting user/org, read
from the contextvars in `app.core.request_id`. A redaction pass runs over the
fully-rendered line, not over individual fields, so a bearer token, a
password, or an email address landing in *any* field — the message, a
traceback, a future engineer's `extra` payload — is caught the same way. That
makes the guarantee independent of every call site getting it right.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

from app.core.request_id import get_request_id, get_request_org_id, get_request_user_id

# Order matters: token/password markers run first so a redacted token can't
# leave behind a fragment that then reads as a plausible (unmasked) email.
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-_.=]+", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r'("?authorization"?\s*[:=]\s*"?)[^\s"\',}]+', re.IGNORECASE)
_PASSWORD_RE = re.compile(r'("?password"?\s*[:=]\s*"?)[^\s"\',}]+', re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _mask_email(match: re.Match[str]) -> str:
    local, _, domain = match.group(0).partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def redact_text(text: str) -> str:
    """Scrub bearer tokens, password fields and email addresses from one line."""
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = _PASSWORD_RE.sub(r"\1[REDACTED]", text)
    text = _EMAIL_RE.sub(_mask_email, text)
    return text


class JsonFormatter(logging.Formatter):
    """Renders every log record as one redacted JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
            "user_id": get_request_user_id(),
            "org_id": get_request_org_id(),
        }
        extra = getattr(record, "json_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        payload = {k: v for k, v in payload.items() if v is not None}
        return redact_text(json.dumps(payload, default=str, sort_keys=True))


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger and align uvicorn's own.

    Idempotent — safe to call more than once (the test suite builds several
    app instances per process).
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn's own loggers (access/error) attach their own coloured handler
    # by default; drop it and let records propagate to our root handler so
    # every line — app and server alike — gets the same JSON shape.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
