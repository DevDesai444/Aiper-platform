"""Mapping a free-text organisation name onto a tenant row.

The slug rule here is deliberately identical to the one migration 0002 used to
group existing accounts, so somebody registering with "Acme Inc" after the
migration joins the organisation their colleagues were already placed in
rather than founding a second one.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organisation

DEFAULT_ORG_NAME = "Default"
DEFAULT_ORG_SLUG = "default"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, runs of non-alphanumerics to a single dash, dashes trimmed.

    Names that reduce to nothing ("", "!!!") fall back to the catch-all
    organisation, matching the migration.
    """
    slug = _NON_ALNUM.sub("-", name.strip().lower()).strip("-")
    return slug or DEFAULT_ORG_SLUG


async def ensure_organisation(db: AsyncSession, name: str) -> Organisation:
    """Find the organisation for this name, creating it the first time."""
    label = name.strip() or DEFAULT_ORG_NAME
    slug = slugify(name)

    existing = (
        await db.execute(select(Organisation).where(Organisation.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    org = Organisation(name=label, slug=slug)
    db.add(org)
    await db.flush()
    return org
