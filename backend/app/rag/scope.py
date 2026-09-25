"""Who may retrieve what. Computed fresh, never cached.

A `RetrievalScope` is the tenancy boundary every vector query must carry:
the caller's organisation, always, and inside it the union of what they own
and the projects the access resolver admits them to. It deliberately is not
a list of file ids — `owner_id` and `project_id` are pinned onto every
Qdrant point at indexing time, so the accessible set is expressed as two
payload predicates and never travels as an unbounded id list, no matter how
many files a user can reach.

The project set comes from `aiper_effective_access` via
`app.services.permissions.has_access`; the cascade is not reimplemented
here. Nothing is memoised: a scope is computed per call, so revoking a
grant is effective on the caller's next query — including the next tool
call inside an agent turn that is already running.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import FileAsset, Project
from app.services.permissions import has_access

ScopeProvider = Callable[[], Awaitable["RetrievalScope"]]


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """The filter every vector query is pinned to.

    Frozen on purpose: a scope is derived from the database and then closed
    over by the agent's tools — nothing downstream can widen it. A scope
    whose org_id is None admits nothing at all.
    """

    org_id: uuid.UUID | None
    user_id: uuid.UUID
    project_ids: tuple[uuid.UUID, ...] = ()


async def compute_scope(
    db: AsyncSession, *, user_id: uuid.UUID, org_id: uuid.UUID | None
) -> RetrievalScope:
    """One query: every project in the caller's organisation the resolver admits."""
    if org_id is None:
        return RetrievalScope(org_id=None, user_id=user_id)
    rows = await db.execute(
        select(Project.id).where(
            Project.org_id == org_id,
            has_access("project", Project.id, user_id),
        )
    )
    return RetrievalScope(
        org_id=org_id, user_id=user_id, project_ids=tuple(rows.scalars().all())
    )


def scope_provider(*, user_id: uuid.UUID, org_id: uuid.UUID | None) -> ScopeProvider:
    """A provider that recomputes the scope on every call, on its own session.

    This is what the agent's tools close over: each retrieval re-derives the
    accessible set, so a grant revoked halfway through a long turn is gone by
    the very next tool call.
    """

    async def fresh() -> RetrievalScope:
        from app.db.base import user_scoped_session

        # Identity-bound: under row-level security an anonymous session would
        # see no projects at all and silently shrink the scope to nothing.
        async with user_scoped_session(user_id) as db:
            return await compute_scope(db, user_id=user_id, org_id=org_id)

    return fresh


def accessible_files_clause(
    *, user_id: uuid.UUID, org_id: uuid.UUID
) -> ColumnElement[bool]:
    """SQL predicate: the file_assets rows this user may read.

    The same rule the vector filter enforces, expressed over the relational
    rows — org boundary first, then own uploads or a project the resolver
    admits. Used to validate attachment ids before they ever reach a tool.
    """
    return and_(
        FileAsset.org_id == org_id,
        or_(
            FileAsset.owner_id == user_id,
            and_(
                FileAsset.project_id.is_not(None),
                has_access("project", FileAsset.project_id, user_id),
            ),
        ),
    )
