"""Idempotent seeding of the built-in templates on first boot.

Built-in templates are global rows (``owner_id`` NULL, ``is_builtin`` true) and
the row-level-security policies deliberately give the runtime role no way to
write such a row — a user must not be able to publish a "built-in". Seeding
therefore runs on the admin engine (the same credentials the entrypoint just
migrated with) when one is configured, and falls back to the app engine for
setups that still connect with a single privileged role.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import DocumentTemplate
from app.services.templates import BUILTIN_TEMPLATES


async def seed_templates() -> None:
    if settings.alembic_database_url:
        engine = create_async_engine(settings.alembic_database_url, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as db:
                await _seed(db)
        finally:
            await engine.dispose()
    else:
        async with SessionLocal() as db:
            await _seed(db)


async def _seed(db: AsyncSession) -> None:
    existing = set(
        (
            await db.execute(
                select(DocumentTemplate.key).where(DocumentTemplate.is_builtin.is_(True))
            )
        )
        .scalars()
        .all()
    )
    for spec in BUILTIN_TEMPLATES:
        if spec["key"] in existing:
            continue
        db.add(DocumentTemplate(**spec, is_builtin=True, owner_id=None))
    await db.commit()
