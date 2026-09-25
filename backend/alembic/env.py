"""Alembic environment, wired to the application's async engine.

The URL is resolved here rather than in alembic.ini so that the container,
a developer's laptop and CI all take the same code path.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from app.config import settings

# Importing the models registers every mapper on Base.metadata; without this
# --autogenerate would see an empty schema and try to drop the world.
from app.db import models  # noqa: F401
from app.db.base import Base
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """-x db_url=... wins, then $ALEMBIC_DATABASE_URL, then app settings."""
    from_cli = (context.get_x_argument(as_dictionary=True) or {}).get("db_url")
    return from_cli or os.getenv("ALEMBIC_DATABASE_URL") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool, future=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
