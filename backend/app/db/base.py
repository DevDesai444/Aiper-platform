"""Async engine, session factory, the schema contract — and the RLS identity.

Row-level security (migration 0003) keys every policy off the transaction-local
GUC ``app.user_id``. The rules for that GUC live here:

* It is set with ``SELECT set_config('app.user_id', <uuid>, true)`` — the
  parameterised equivalent of ``SET LOCAL``. Transaction-local is the only safe
  scope on pooled connections: a session-level ``SET`` would survive the
  transaction, follow the connection back into the pool, and hand one request's
  identity to the next. ``set_config(..., true)`` dies at COMMIT or ROLLBACK,
  so a recycled connection is always anonymous.

* It is re-armed on **every** transaction. Application code commits mid-request
  (lazy provisioning, multi-step routes), and each commit would silently strip
  a once-per-request setting. The ``after_begin`` session event fires when a
  session opens a new database transaction, before the statement that triggered
  it, so no query in an identity-bound session ever runs without the GUC.

* A session with no bound identity sets nothing, and every policy denies. That
  is the failure posture: forgetting to bind is a visible 404/empty-set bug,
  never a cross-tenant read.
"""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_IDENTITY_KEY = "rls_user_id"
_SET_IDENTITY = text("SELECT set_config('app.user_id', :user_id, true)")


@event.listens_for(Session, "after_begin")
def _arm_identity(session: Session, transaction, connection) -> None:
    """Stamp the bound identity onto every new transaction of every session.

    Registered on the Session class so the admin engine, test engines and the
    app engine all share one rule: sessions without an identity in ``info``
    (migrations, seeding, fixtures) are simply left anonymous.
    """
    user_id = session.info.get(_IDENTITY_KEY)
    if user_id is not None:
        connection.execute(_SET_IDENTITY, {"user_id": str(user_id)})


def user_scoped_session(user_id: uuid.UUID | str) -> AsyncSession:
    """A session whose every transaction carries this user's identity.

    For work outside the request dependency — the post-stream persist of an
    agent turn is the one current caller.
    """
    return SessionLocal(info={_IDENTITY_KEY: str(user_id)})


async def bind_session_identity(session: AsyncSession, user_id: uuid.UUID | str) -> None:
    """Bind an identity to an already-open session, current transaction included.

    The legacy auth endpoints start anonymous by design: registration binds the
    id it just minted before inserting the row (the users INSERT policy admits
    exactly ``id = app.user_id``), and login binds after the password check so
    the audit trail can be written as the user who logged in.
    """
    session.info[_IDENTITY_KEY] = str(user_id)
    if session.in_transaction():
        await session.execute(_SET_IDENTITY, {"user_id": str(user_id)})


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def verify_schema() -> None:
    """Fail fast and legibly if the database has not been migrated.

    The schema is owned by Alembic, not by the application: `alembic upgrade
    head` runs in the container entrypoint before uvicorn. This replaces the
    old `Base.metadata.create_all` bootstrap, which could not express the
    tenancy backfills and silently diverged from the migration history.
    """
    unmigrated = RuntimeError(
        "The database is not migrated: no Alembic revision is stamped. "
        "Run `alembic upgrade head` from backend/ before starting the API."
    )
    try:
        async with engine.connect() as conn:
            revision = (
                await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            ).scalar_one_or_none()
    except DatabaseError as exc:  # the alembic_version table itself is missing
        raise unmigrated from exc

    if revision is None:
        raise unmigrated

    # A stamp alone is not enough: a database stamped at an older revision has
    # tables the mappers no longer match, which surfaces as 500s at request
    # time instead of one legible refusal here.
    head = _migration_head()
    if head is not None and revision != head:
        raise RuntimeError(
            f"The database is at Alembic revision {revision!r} but the code "
            f"expects {head!r}. Run `alembic upgrade head` from backend/ "
            "before starting the API."
        )


def _migration_head() -> str | None:
    """The newest revision shipped with this code, or None off a checkout/image
    that carries no alembic/ directory (tests import this module without one)."""
    versions = Path(__file__).resolve().parents[2] / "alembic"
    if not versions.is_dir():
        return None
    from alembic.script import ScriptDirectory

    return ScriptDirectory(str(versions)).get_current_head()
