"""Async engine, session factory and the schema contract."""

from collections.abc import AsyncIterator

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
