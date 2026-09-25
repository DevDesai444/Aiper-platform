"""Shared fixtures.

The tests are hermetic:

* **Database** — a real Postgres (``TEST_DATABASE_URL``, default the CI service on
  localhost:5432). Every run creates its own throwaway database, migrates it with
  the real ``alembic upgrade head`` — which puts the migrations themselves under
  test — and drops it at the end. Each test starts from empty tables.

  The schema comes from migrations rather than ``Base.metadata.create_all``
  because it is not expressible in the mappers: the access resolver
  ``aiper_effective_access`` is a SQL function defined in migration 0002, and
  ``create_all`` would leave it out.

* **Tokens** — HS256 tokens are minted in-process with a test secret, so Supabase
  verification runs its full claim checks without any network / JWKS fetch.
* **Settings** — every test starts with *no* auth mode configured; a test opts
  into the Supabase or legacy path via a fixture, and state is reset afterwards.

The app under test is a minimal FastAPI instance mounting the *real* routers (and
therefore the real ``current_user`` dependency and verifier). This keeps the
suite fast and free of the agent/vector-store stack while exercising the exact
code paths that run in production.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
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

BACKEND_DIR = Path(__file__).resolve().parents[1]


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


# ────────────────────────────── migrated database ─────────────────────────────


def with_database(dsn: str, name: str) -> str:
    """Swap the database name in a DSN, keeping credentials and host."""
    head, _, tail = dsn.rpartition("/")
    query = tail.partition("?")[2]
    return f"{head}/{name}" + (f"?{query}" if query else "")


async def _execute(dsn: str, statement: str) -> None:
    # CREATE/DROP DATABASE cannot run inside a transaction.
    engine = create_async_engine(dsn, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


def alembic_upgrade(dsn: str, revision: str = "head") -> subprocess.CompletedProcess[bytes]:
    """Run the real migrations against `dsn`, failing loudly with their output."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND_DIR,
        env={**os.environ, "ALEMBIC_DATABASE_URL": dsn},
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade {revision} failed:\n"
            + result.stdout.decode(errors="replace")
            + result.stderr.decode(errors="replace")
        )
    return result


@pytest.fixture(scope="session")
def scratch_database() -> Iterator[Callable[[str], str]]:
    """Hands out throwaway databases and drops them when the session ends.

    Synchronous on purpose: database creation must not share an event loop with
    the tests, which each run on their own.
    """
    admin_dsn = with_database(TEST_DATABASE_URL, "postgres")
    created: list[str] = []

    def make(prefix: str = "aiper_test") -> str:
        name = f"{prefix}_{uuid.uuid4().hex[:12]}"
        asyncio.run(_execute(admin_dsn, f'CREATE DATABASE "{name}"'))
        created.append(name)
        return with_database(TEST_DATABASE_URL, name)

    try:
        yield make
    finally:
        for name in created:
            try:
                asyncio.run(_execute(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            except Exception as exc:  # noqa: BLE001 - never mask a test failure
                print(f"warning: could not drop test database {name}: {exc}")


@pytest.fixture(scope="session")
def migrated_dsn(scratch_database: Callable[[str], str]) -> str:
    """A database at `alembic upgrade head`, shared by the whole session."""
    dsn = scratch_database("aiper_test")
    alembic_upgrade(dsn)
    return dsn


async def _truncate_everything(engine) -> None:
    """Empty every table, leaving the schema (and alembic_version) in place.

    Discovered rather than listed, so a new table added by a later migration is
    cleaned up without anyone having to remember to add it here.
    """
    async with engine.begin() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables"
                        " WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            await conn.execute(text(f"TRUNCATE {quoted} CASCADE"))


@pytest_asyncio.fixture
async def engine(migrated_dsn: str):
    eng = create_async_engine(migrated_dsn, poolclass=NullPool, future=True)
    try:
        await _truncate_everything(eng)
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    """A session for arranging a scenario. Commit before calling a route."""
    async with session_factory() as session:
        yield session


# ──────────────────────────────── app under test ──────────────────────────────


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncClient:
    """The auth router, with the real current_user dependency."""
    from app.api import auth as auth_module
    from app.core.deps import get_session

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


class Api:
    """The tenancy routes, with authentication stubbed out.

    app.main is deliberately not imported: it pulls in the agent runtime and the
    vector store, neither of which has anything to do with permissions.
    """

    def __init__(self, client: AsyncClient, set_user: Callable[[object], None]) -> None:
        self.client = client
        self._set_user = set_user

    def as_user(self, user: object) -> AsyncClient:
        self._set_user(user)
        return self.client


@pytest_asyncio.fixture
async def api(session_factory) -> AsyncIterator[Api]:
    from app.api import documents, projects
    from app.core.deps import current_user, get_session

    app = FastAPI()
    versioned = APIRouter(prefix="/api/v1")
    versioned.include_router(projects.router)
    versioned.include_router(documents.router)
    app.include_router(versioned)

    # A session per request, as in production. Sharing the arranging session
    # would let a stale identity map answer questions the database should,
    # which hides exactly the kind of bug these tests are looking for.
    async def request_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    holder: dict[str, object] = {}
    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[current_user] = lambda: holder["user"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield Api(client, lambda user: holder.__setitem__("user", user))
    app.dependency_overrides.clear()


# ─────────────────────────── the unprivileged role ────────────────────────────
#
# Everything above connects as the migration user — in CI and dev that is the
# bootstrap superuser, which Postgres exempts from row-level security entirely.
# The fixtures below connect as aiper_app, the role the API really runs as, so
# a test that uses them is subject to every policy of migrations 0003/0004.

APP_ROLE_USER = "aiper_app"
APP_ROLE_PASSWORD = os.environ.get("AIPER_APP_DB_PASSWORD", "aiper_app_password")


def with_credentials(dsn: str, user: str, password: str) -> str:
    """Swap the credentials in a DSN, keeping scheme, host and database."""
    scheme, _, rest = dsn.partition("://")
    _, _, host_part = rest.rpartition("@")
    return f"{scheme}://{user}:{password}@{host_part}"


@pytest_asyncio.fixture
async def app_engine(engine, migrated_dsn: str):
    """An engine connected as aiper_app. Depends on `engine` for the truncate."""
    eng = create_async_engine(
        with_credentials(migrated_dsn, APP_ROLE_USER, APP_ROLE_PASSWORD),
        poolclass=NullPool,
        future=True,
    )
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def app_session_factory(app_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False, class_=AsyncSession)


def as_user(factory: async_sessionmaker[AsyncSession], user) -> AsyncSession:
    """An app-role session carrying this user's identity, as the API would open it.

    Every transaction it begins runs set_config('app.user_id', …, true) first —
    the same event listener production uses (app.db.base). Takes a User or a
    bare id, so a test can keep acting after a rollback expired its instances.
    """
    return factory(info={"rls_user_id": str(getattr(user, "id", user))})


def anonymous(factory: async_sessionmaker[AsyncSession]) -> AsyncSession:
    """An app-role session with no identity: the deny-all baseline."""
    return factory()


@pytest_asyncio.fixture
async def rls_api(app_session_factory) -> AsyncIterator[Api]:
    """The tenancy routes on the *unprivileged* engine, policies live.

    Unlike `api`, the request sessions here connect as aiper_app and carry the
    acting user's identity, so every route in these tests runs with row-level
    security enforced underneath the application checks.
    """
    from app.api import audit as audit_module
    from app.api import documents, projects
    from app.core.deps import current_user, get_session

    app = FastAPI()
    versioned = APIRouter(prefix="/api/v1")
    versioned.include_router(projects.router)
    versioned.include_router(documents.router)
    versioned.include_router(audit_module.router)
    app.include_router(versioned)

    holder: dict[str, object] = {}

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with as_user(app_session_factory, holder["user"]) as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[current_user] = lambda: holder["user"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield Api(client, lambda user: holder.__setitem__("user", user))
    app.dependency_overrides.clear()
