"""Resolving a short id (or a full uuid) out of a human-readable URL segment.

The frontend's `/projects/{slug}-{id8}` puts a slug ahead of an 8-hex-character
id in the path; the slug is cosmetic and never reaches here — the frontend
strips it before calling the API, so this module only ever sees either a full
uuid (every link minted before slugging existed, and every link the app
itself builds once it already has one in hand) or a bare 8-hex string.

Resolving the short form is a lookup, not an authorisation check: it narrows a
prefix to a single row *within the caller's own organisation*, exactly the
same kind of index-friendly pre-filter `list_projects` already uses ahead of
the resolver. Whatever this returns still goes through `require_access` (or
the resolver) before anything is read or written — a row this function finds
is not yet a row the caller may see.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Folder, Project

_ID8 = re.compile(r"^[0-9a-f]{8}$", re.IGNORECASE)


async def resolve_ref(
    db: AsyncSession, kind: str, org_id: uuid.UUID, ref: str
) -> uuid.UUID | None:
    """A path segment's id — full uuid or 8-hex prefix — to a real row's id.

    A full uuid is returned as-is with no query: the overwhelmingly common
    case once a page has loaded and starts building its own links, and every
    bare-uuid bookmark from before this scheme existed.

    An 8-hex string is looked up by prefix, scoped to the caller's
    organisation. No new index backs this: the organisation boundary already
    narrows the scan to a tiny number of rows, and at 8 hex characters two
    rows sharing a prefix within one organisation is cosmically unlikely — if
    it ever happens, both matches are refused rather than one guessed at
    (`None`, not a pick), which the caller reports as "not found" exactly
    like any other unresolved reference.
    """
    try:
        return uuid.UUID(ref)
    except ValueError:
        pass

    if not _ID8.match(ref):
        return None

    prefix = f"{ref.lower()}%"
    if kind == "project":
        query = select(Project.id).where(
            Project.org_id == org_id, cast(Project.id, String).like(prefix)
        )
    elif kind == "document":
        query = select(Document.id).where(
            Document.org_id == org_id, cast(Document.id, String).like(prefix)
        )
    elif kind == "folder":
        # Folders carry no org_id of their own — only their project does.
        query = (
            select(Folder.id)
            .join(Project, Project.id == Folder.project_id)
            .where(Project.org_id == org_id, cast(Folder.id, String).like(prefix))
        )
    else:
        raise ValueError(f"resolve_ref: unknown kind {kind!r}")

    rows = (await db.execute(query)).scalars().all()
    return rows[0] if len(rows) == 1 else None
