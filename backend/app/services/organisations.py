"""Mapping a free-text organisation name onto a tenant row.

The slug rule here is deliberately identical to the one migration 0002 used to
group existing accounts, so somebody registering with "Acme Inc" after the
migration joins the organisation their colleagues were already placed in
rather than founding a second one.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organisation

DEFAULT_ORG_NAME = "Default"
DEFAULT_ORG_SLUG = "default"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_ENSURE = text("SELECT * FROM aiper_ensure_organisation(:name, :slug)")


def slugify(name: str) -> str:
    """Lowercase, runs of non-alphanumerics to a single dash, dashes trimmed.

    Names that reduce to nothing ("", "!!!") fall back to the catch-all
    organisation, matching the migration.
    """
    slug = _NON_ALNUM.sub("-", name.strip().lower()).strip("-")
    return slug or DEFAULT_ORG_SLUG


async def ensure_organisation(db: AsyncSession, name: str) -> Organisation:
    """Find the organisation for this name, creating it the first time.

    An upsert, because this sits on the sign-in path: the first two concurrent
    logins would otherwise race on the unique slug, and a failed INSERT poisons
    the surrounding transaction rather than being recoverable.

    It runs through ``aiper_ensure_organisation`` (SECURITY DEFINER, migration
    0003): both callers — registration and lazy provisioning — act before the
    account exists, which is exactly the moment row-level security shows them
    no organisation at all. The slug stays computed here, in the one Python
    slug rule the migration was verified against.
    """
    label = name.strip() or DEFAULT_ORG_NAME
    slug = slugify(name)

    row = (await db.execute(_ENSURE, {"name": label, "slug": slug})).mappings().one()
    # A detached value object: the row already exists, adding it to the
    # session would try to insert it again. Callers only read from it.
    return Organisation(**dict(row))
