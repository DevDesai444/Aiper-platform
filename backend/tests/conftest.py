"""Shared fixtures for the auth test suite.

The tests are hermetic:

* **Database** — a real Postgres (``TEST_DATABASE_URL``, default the CI service on
  localhost:5432). Each test gets a freshly created-and-dropped schema, so there
  is no cross-test state.
* **Tokens** — HS256 tokens are minted in-process with a test secret, so Supabase
  verification runs its full claim checks without any network / JWKS fetch.
* **Settings** — every test starts with *no* auth mode configured; a test opts
  into the Supabase or legacy path via a fixture, and state is reset afterwards.

The app under test is a minimal FastAPI instance that mounts the *real* auth
router (and therefore the real ``current_user`` dependency and verifier). This
keeps the suite fast and free of the agent/vector-store stack while exercising
the exact code paths that run in production.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Set safe env BEFORE any app import so the cached get_settings() picks them up
# (from E10's Phase 0 conftest; merged at integration).
os.environ.setdefault("AIPER_DEV_MODE", "1")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://aiper_user:aiper_password@localhost:5432/aiper_db",
)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://aiper_user:aiper_password@localhost:5432/aiper_db",
)

TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_SUPABASE_SECRET = "test-supabase-jwt-secret-value-do-not-use-in-prod"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"
TEST_AUDIENCE = "authenticated"


# ─────────────────────────────── token helpers ────────────────────────────────


def mint_hs256(
    *,
    sub: str | uuid.UUID | None = None,
    secret: str = TEST_SUPABASE_SECRET,
    issuer: str | None = TEST_ISSUER,
    audience: str | None = TEST_AUDIENCE,
    email: str | None = "user@example.com",
    full_name: str | None = None,
    expires_in: int = 3600,
) -> str:
    """Mint an HS256 Supabase-style access token. Omit a field (pass None) to drop it."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {"iat": now, "exp": now + timedelta(seconds=expires_in)}
    if sub is not None:
        payload["sub"] = str(sub)
    if issuer is not None:
        payload["iss"] = issuer
    if audience is not None:
        payload["aud"] = audience
    if email is not None:
        payload["email"] = email
    if full_name is not None:
        payload["user_metadata"] = {"full_name": full_name}
    return jwt.encode(payload, secret, algorithm="HS256")


def mint_alg_none(*, sub: str | uuid.UUID | None = None) -> str:
    """Craft an unsigned ``alg=none`` token (a classic JWT bypass attempt)."""

    def _seg(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = datetime.now(timezone.utc)
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "sub": str(sub or uuid.uuid4()),
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    return f"{_seg(header)}.{_seg(payload)}."


@pytest.fixture
def mint_token() -> Callable[..., str]:
    return mint_hs256


# ──────────────────────────────── settings state ──────────────────────────────


@pytest.fixture(autouse=True)
def reset_auth_settings():
    """Force a known 'no auth configured' baseline around every test.

    Independent of any local backend/.env, so the suite behaves identically in CI
    and on a developer machine that has real Supabase values configured.
    """
    from app.config import settings

    def baseline() -> None:
        settings.supabase_url = ""
        settings.supabase_jwt_secret = ""
        settings.supabase_jwks_url = ""
        settings.auth_legacy_login_enabled = False

    baseline()
    yield
    baseline()
    from app.api.auth import login_rate_limiter

    login_rate_limiter.reset()
    from app.core.supabase_auth import _jwks_client

    _jwks_client.cache_clear()


@pytest.fixture
def supabase_configured():
    """Put the app in production (Supabase) mode with the HS256 test secret."""
    from app.config import settings

    settings.supabase_url = TEST_SUPABASE_URL
    settings.supabase_jwt_secret = TEST_SUPABASE_SECRET
    settings.supabase_jwks_url = f"{TEST_ISSUER}/.well-known/jwks.json"
    settings.auth_legacy_login_enabled = False
    return settings


@pytest.fixture
def legacy_configured():
    """Put the app in legacy (dev/demo) mode: no Supabase, self-issued login on."""
    from app.config import settings

    settings.supabase_url = ""
    settings.supabase_jwt_secret = ""
    settings.supabase_jwks_url = ""
    settings.auth_legacy_login_enabled = True
    return settings


# ──────────────────────────────── database / app ──────────────────────────────


@pytest_asyncio.fixture
async def engine():
    import app.db.models  # noqa: F401 - register every table on Base.metadata
    from app.db.base import Base

    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncClient:
    from app.api import auth as auth_module
    from app.db.base import get_session

    async def _get_test_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(auth_module.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = _get_test_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
