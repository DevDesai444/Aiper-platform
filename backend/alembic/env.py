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

# Constraints migration 0003 manages beyond what the mappers express: the
# composite foreign keys that pin a folder reference to its project need
# (id, project_id) reference pairs, which the ORM relationships deliberately do
# not model (a composite self-join would write project_id along two paths).
# Autogenerate must neither drop the composite constraints nor recreate the
# single-column ones the mappers still carry for the join conditions.
_DB_MANAGED_CONSTRAINTS = {
    "fk_documents_folder_project",
    "fk_folders_parent_project",
    "uq_folders_id_project",
}
_MAPPER_ONLY_FKS = {
    ("documents", ("folder_id",)),
    ("folders", ("parent_folder_id",)),
}


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ in {"foreign_key_constraint", "unique_constraint"}:
        if reflected and name in _DB_MANAGED_CONSTRAINTS:
            return False
        if not reflected and type_ == "foreign_key_constraint":
            columns = tuple(col.name for col in obj.columns)
            if (obj.table.name, columns) in _MAPPER_ONLY_FKS:
                return False
    return True


def _database_url() -> str:
    """-x db_url=... wins, then $ALEMBIC_DATABASE_URL, then app settings.

    settings.database_url is the last resort: since migration 0003 it names the
    unprivileged runtime role, which cannot run DDL — real deployments (and the
    container entrypoint) provide ALEMBIC_DATABASE_URL.
    """
    from_cli = (context.get_x_argument(as_dictionary=True) or {}).get("db_url")
    return (
        from_cli
        or os.getenv("ALEMBIC_DATABASE_URL")
        or settings.alembic_database_url
        or settings.database_url
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
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
