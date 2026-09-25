"""Async engine, session factory and the schema contract."""

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


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
