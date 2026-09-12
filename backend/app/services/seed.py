"""Idempotent seeding of the built-in templates on first boot."""

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import DocumentTemplate
from app.services.templates import BUILTIN_TEMPLATES


async def seed_templates() -> None:
    async with SessionLocal() as db:
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
