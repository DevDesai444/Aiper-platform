"""Log-hygiene coverage for structured logging.

No Postgres: this exercises `app.core.request_id.RequestIDMiddleware` and
`app.core.logging_config` against a minimal app with no DB or auth machinery
(the same reasoning `conftest.py` gives for keeping `app.main` out of the
fixtures — this suite has nothing to do with permissions either). A bearer
token, a password, or an email must never survive into a rendered log line,
including from a route that logs one the careless way, since the redaction
pass is the backstop for exactly that call site, not just the well-behaved
ones.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from app.core.logging_config import JsonFormatter, redact_text
from app.core.request_id import (
    RequestIDMiddleware,
    bind_request_identity,
    get_request_id,
    get_request_org_id,
    get_request_user_id,
)
from fastapi import APIRouter, FastAPI, Request
from httpx import ASGITransport, AsyncClient

_PROBE_LOGGER_NAME = "tests.log_hygiene_probe"
_probe_logger = logging.getLogger(_PROBE_LOGGER_NAME)

PASSWORD_SECRET = "hunter2-super-secret"
CONTACT_EMAIL = "devchira@buffalo.edu"


# ────────────────────────────── redact_text unit tests ────────────────────────


class TestRedactText:
    def test_bearer_token_is_masked(self) -> None:
        secret = "sk-ABCDEF1234567890"
        result = redact_text(f"Authorization: Bearer {secret}")
        assert secret not in result
        assert "[REDACTED]" in result

    def test_password_field_is_masked_but_sibling_fields_survive(self) -> None:
        result = redact_text('{"username": "e7", "password": "hunter2-super-secret"}')
        assert "hunter2-super-secret" not in result
        assert "[REDACTED]" in result
        assert '"username": "e7"' in result

    def test_email_is_partially_masked(self) -> None:
        result = redact_text(f"contact {CONTACT_EMAIL} for access")
        assert CONTACT_EMAIL not in result
        assert "de***@buffalo.edu" in result

    def test_short_local_part_email_does_not_crash(self) -> None:
        result = redact_text("reach a@b.com now")
        assert "a@b.com" not in result
        assert "a***@b.com" in result

    def test_unrelated_text_is_unchanged(self) -> None:
        line = "agent turn completed in 1.2s, 340 tokens"
        assert redact_text(line) == line


# ───────────────────────────── JsonFormatter unit tests ───────────────────────


class TestJsonFormatterShape:
    def _record(self, msg: str, *args: object) -> logging.LogRecord:
        return logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_formats_valid_json_with_expected_fields(self) -> None:
        line = JsonFormatter().format(self._record("hello %s", "world"))
        payload = json.loads(line)
        assert payload["message"] == "hello world"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert "timestamp" in payload

    def test_redacts_inside_the_rendered_message(self) -> None:
        line = JsonFormatter().format(self._record("token leaked: Bearer abc123secret"))
        assert "abc123secret" not in line
        assert "[REDACTED]" in json.loads(line)["message"]

    def test_context_free_fields_are_omitted_not_null(self) -> None:
        """Outside any request (no contextvar bound), the identity fields are
        left out entirely rather than serialised as `null` noise."""
        payload = json.loads(JsonFormatter().format(self._record("no request here")))
        assert "request_id" not in payload
        assert "user_id" not in payload
        assert "org_id" not in payload


# ──────────────────────────── end-to-end: real middleware ─────────────────────


class _CollectingHandler(logging.Handler):
    """Renders every record through the real JsonFormatter and keeps the lines.

    Attached directly to specific loggers for the duration of one test, never
    to the root logger — so it can't clobber pytest's own log capture or leak
    between tests the way replacing `logging.getLogger().handlers` would.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def capture_logs() -> Iterator[_CollectingHandler]:
    handler = _CollectingHandler()
    watched = [logging.getLogger("aiper.access"), _probe_logger]
    previous_levels = [lg.level for lg in watched]
    for lg in watched:
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        for lg, level in zip(watched, previous_levels, strict=True):
            lg.removeHandler(handler)
            lg.setLevel(level)


def _build_probe_app() -> FastAPI:
    """No DB, no auth dependency — just the real middleware plus routes that
    stand in for (a) a careless call site echoing a header into a log line,
    and (b) the moment `app.core.deps.current_user` binds identity."""
    router = APIRouter()

    @router.get("/probe")
    async def probe(request: Request) -> dict:
        _probe_logger.info(
            "probe request: authorization=%s password=%s contact=%s",
            request.headers.get("authorization"),
            PASSWORD_SECRET,
            CONTACT_EMAIL,
        )
        return {
            "request_id": get_request_id(),
            "user_id": get_request_user_id(),
            "org_id": get_request_org_id(),
        }

    @router.get("/probe-with-identity")
    async def probe_with_identity() -> dict:
        # Mirrors the one-line call added in app.core.deps.current_user.
        bind_request_identity(user_id=uuid.UUID(int=1), org_id=uuid.UUID(int=2))
        _probe_logger.info("identity bound")
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(RequestIDMiddleware)
    return app


@pytest_asyncio.fixture
async def probe_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestLogHygieneEndToEnd:
    @pytest.mark.asyncio
    async def test_bearer_token_never_appears_in_any_captured_log_line(
        self, probe_client: AsyncClient, capture_logs: _CollectingHandler
    ) -> None:
        secret = f"sk-{uuid.uuid4().hex}"
        response = await probe_client.get(
            "/probe", headers={"Authorization": f"Bearer {secret}"}
        )
        assert response.status_code == 200
        assert secret not in capture_logs.text
        assert "[REDACTED]" in capture_logs.text

    @pytest.mark.asyncio
    async def test_password_and_email_also_never_appear(
        self, probe_client: AsyncClient, capture_logs: _CollectingHandler
    ) -> None:
        response = await probe_client.get("/probe")
        assert response.status_code == 200
        assert PASSWORD_SECRET not in capture_logs.text
        assert CONTACT_EMAIL not in capture_logs.text
        assert "de***@buffalo.edu" in capture_logs.text

    @pytest.mark.asyncio
    async def test_missing_authorization_header_does_not_crash_the_formatter(
        self, probe_client: AsyncClient, capture_logs: _CollectingHandler
    ) -> None:
        """A request with nothing to redact must not blow up the formatter.

        The `authorization=` marker matches regardless of what follows —
        including the literal word "None" — so this also over-redacts a
        header that was never sent. That is the intended, safe direction to
        be wrong in: the alternative is a pattern narrow enough to one day
        leave a real token unmasked.
        """
        response = await probe_client.get("/probe")
        assert response.status_code == 200
        assert "authorization=[REDACTED]" in capture_logs.text

    @pytest.mark.asyncio
    async def test_response_carries_a_request_id_header_matching_the_body(
        self, probe_client: AsyncClient
    ) -> None:
        response = await probe_client.get("/probe")
        assert "x-request-id" in response.headers
        assert response.json()["request_id"] == response.headers["x-request-id"]

    @pytest.mark.asyncio
    async def test_client_supplied_request_id_is_adopted(
        self, probe_client: AsyncClient
    ) -> None:
        mine = "caller-supplied-id-123"
        response = await probe_client.get("/probe", headers={"X-Request-ID": mine})
        assert response.headers["x-request-id"] == mine

    @pytest.mark.asyncio
    async def test_two_requests_get_two_different_ids(self, probe_client: AsyncClient) -> None:
        first = await probe_client.get("/probe")
        second = await probe_client.get("/probe")
        assert first.headers["x-request-id"] != second.headers["x-request-id"]

    @pytest.mark.asyncio
    async def test_access_log_line_omits_identity_when_unauthenticated(
        self, probe_client: AsyncClient, capture_logs: _CollectingHandler
    ) -> None:
        await probe_client.get("/probe")
        access_lines = [line for line in capture_logs.lines if '"event": "http_request"' in line]
        assert access_lines
        payload = json.loads(access_lines[-1])
        assert payload["status_code"] == 200
        assert payload["method"] == "GET"
        assert "user_id" not in payload

    @pytest.mark.asyncio
    async def test_identity_bound_mid_request_reaches_the_next_log_line(
        self, probe_client: AsyncClient, capture_logs: _CollectingHandler
    ) -> None:
        response = await probe_client.get("/probe-with-identity")
        assert response.status_code == 200
        identity_lines = [line for line in capture_logs.lines if "identity bound" in line]
        assert identity_lines
        payload = json.loads(identity_lines[0])
        assert payload["user_id"] == str(uuid.UUID(int=1))
        assert payload["org_id"] == str(uuid.UUID(int=2))

    @pytest.mark.asyncio
    async def test_identity_does_not_leak_across_requests(
        self, probe_client: AsyncClient, capture_logs: _CollectingHandler
    ) -> None:
        """Each request gets its own contextvar snapshot — binding identity on
        one request must not bleed into the next, unrelated one."""
        await probe_client.get("/probe-with-identity")
        capture_logs.lines.clear()
        await probe_client.get("/probe")
        probe_lines = [line for line in capture_logs.lines if "probe request" in line]
        assert probe_lines
        payload = json.loads(probe_lines[0])
        assert "user_id" not in payload
