"""The permission layer: one question, answered in one place.

`aiper_effective_access` (migration 0002) is the only authority on what a user
may do to a subject. This module calls it and translates the answer into HTTP;
it deliberately does not re-implement the cascade, because two implementations
of an access rule eventually disagree and the disagreement is a vulnerability.

Nothing here is memoised. Every check is a fresh query, so revoking a grant
takes effect on the next call rather than at the end of some cache lifetime.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from sqlalchemy import cast, func, literal, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.deps import CurrentUser, DbSession
from app.db.models import Role, SubjectType, User, subject_enum

# owner > editor > viewer. The resolver applies the same order in SQL; this is
# for comparing the answer against what a route requires.
_RANK: dict[str, int] = {"viewer": 1, "editor": 2, "owner": 3}

_RESOLVE = text(
    "SELECT aiper_effective_access("
    "  CAST(:user_id AS uuid),"
    "  CAST(:subject_type AS aiper_subject),"
    "  CAST(:subject_id AS uuid)"
    ")"
)


async def effective_access(
    db: AsyncSession,
    user_id: uuid.UUID,
    subject_type: SubjectType,
    subject_id: uuid.UUID,
) -> Role | None:
    """The user's role on this subject, or None for no access at all.

    None covers every way a subject can be out of reach — it does not exist,
    it belongs to another organisation, or nothing has been granted — and the
    caller must not distinguish between them.
    """
    role = (
        await db.execute(
            _RESOLVE,
            {
                "user_id": str(user_id),
                "subject_type": subject_type,
                "subject_id": str(subject_id),
            },
        )
    ).scalar_one()
    return role  # type: ignore[return-value]


def access_expression(
    subject_type: SubjectType,
    subject_id: ColumnElement[uuid.UUID],
    user_id: uuid.UUID,
) -> ColumnElement[str | None]:
    """The resolver as a SQL expression, for listings.

    Lets a query select or filter on the effective role instead of
    reimplementing the cascade as a join. The function is STABLE, so it is
    safe in a select list and in WHERE.
    """
    return func.aiper_effective_access(
        literal(user_id, type_=PgUUID(as_uuid=True)),
        cast(literal(subject_type), subject_enum),
        subject_id,
    )


def has_access(
    subject_type: SubjectType,
    subject_id: ColumnElement[uuid.UUID],
    user_id: uuid.UUID,
) -> ColumnElement[bool]:
    """A SQL predicate for "the user can see this row"."""
    return access_expression(subject_type, subject_id, user_id).is_not(None)


async def require_access(
    db: AsyncSession,
    user: User,
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    required: Role,
) -> Role:
    """Authorise, or raise.

    404 when the user has no access at all: a subject they cannot reach must
    be indistinguishable from one that does not exist, or the error itself
    leaks the contents of another organisation.

    403 only once access is established and the role is merely too low — at
    that point the user already knows the subject exists.
    """
    role = await effective_access(db, user.id, subject_type, subject_id)
    if role is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{subject_type.capitalize()} not found"
        )
    if _RANK[role] < _RANK[required]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"This action requires {required} access"
        )
    return role


def requires(
    subject_type: SubjectType, required: Role, *, param: str | None = None
) -> Callable[..., Awaitable[Role]]:
    """Build a route dependency that authorises a path parameter.

        @router.get("/{project_id}", dependencies=[Depends(requires("project", "viewer"))])

    The id is read from the `<subject_type>_id` path parameter unless `param`
    says otherwise. The dependency returns the effective role, so a route that
    needs it can depend on it by value instead of re-querying.
    """
    param_name = param or f"{subject_type}_id"

    async def dependency(request: Request, user: CurrentUser, db: DbSession) -> Role:
        raw = request.path_params.get(param_name)
        try:
            subject_id = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            # A malformed id is treated as a missing subject, not a bad
            # request: the distinction would confirm which ids are real.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"{subject_type.capitalize()} not found"
            ) from None
        return await require_access(db, user, subject_type, subject_id, required)

    return dependency


# ──────────────────────────────── granting ────────────────────────────────


async def grant_access(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    user_id: uuid.UUID,
    role: Role,
    granted_by: uuid.UUID | None = None,
) -> None:
    """Grant, or move an existing grant to a new role.

    One row per (subject, user) — re-granting replaces the role rather than
    stacking a second grant whose precedence would be ambiguous.
    """
    await db.execute(
        text(
            """
            INSERT INTO access_grants
                   (id, org_id, subject_type, subject_id, user_id, role, granted_by, created_at)
            VALUES (gen_random_uuid(),
                    CAST(:org_id AS uuid),
                    CAST(:subject_type AS aiper_subject),
                    CAST(:subject_id AS uuid),
                    CAST(:user_id AS uuid),
                    CAST(:role AS aiper_role),
                    CAST(:granted_by AS uuid),
                    now())
            ON CONFLICT ON CONSTRAINT uq_access_grants_subject_user
            DO UPDATE SET role = EXCLUDED.role, granted_by = EXCLUDED.granted_by
            """
        ),
        {
            "org_id": str(org_id),
            "subject_type": subject_type,
            "subject_id": str(subject_id),
            "user_id": str(user_id),
            "role": role,
            "granted_by": str(granted_by) if granted_by else None,
        },
    )


async def revoke_access(
    db: AsyncSession,
    *,
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await db.execute(
        text(
            """
            DELETE FROM access_grants
             WHERE subject_type = CAST(:subject_type AS aiper_subject)
               AND subject_id   = CAST(:subject_id AS uuid)
               AND user_id      = CAST(:user_id AS uuid)
            """
        ),
        {
            "subject_type": subject_type,
            "subject_id": str(subject_id),
            "user_id": str(user_id),
        },
    )


__all__ = [
    "access_expression",
    "effective_access",
    "grant_access",
    "has_access",
    "require_access",
    "requires",
    "revoke_access",
]
