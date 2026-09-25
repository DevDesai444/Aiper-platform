"""Project tree mechanics, and the invariants the schema cannot express.

A folder's parent must live in the same project as the folder. A composite
foreign key could enforce that only by duplicating project_id into every
parent reference, so it is enforced here instead — and the resolver refuses to
walk out of a project regardless, so a violation could never widen access.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Folder, Project, User
from app.services.permissions import grant_access

DEFAULT_PROJECT_NAME = "Workspace"


async def ensure_default_project(db: AsyncSession, user: User) -> Project:
    """The caller's own Workspace project, created on first use.

    Mirrors what migration 0002 built for existing owners, so a client that
    does not yet know about projects still has somewhere to write.
    """
    project = (
        await db.execute(
            select(Project)
            .where(
                Project.org_id == user.org_id,
                Project.created_by == user.id,
                Project.name == DEFAULT_PROJECT_NAME,
            )
            .order_by(Project.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if project is not None:
        return project

    project = Project(
        org_id=user.org_id,
        name=DEFAULT_PROJECT_NAME,
        description="",
        created_by=user.id,
    )
    db.add(project)
    await db.flush()
    await grant_access(
        db,
        org_id=user.org_id,
        subject_type="project",
        subject_id=project.id,
        user_id=user.id,
        role="owner",
        granted_by=user.id,
    )
    return project


async def resolve_folder(
    db: AsyncSession, project_id: uuid.UUID, folder_id: uuid.UUID | None
) -> Folder | None:
    """Load a folder and assert it belongs to this project.

    Returns None for a None id (meaning the project root). A folder that
    belongs to another project is reported as not found: the caller has no
    business learning that it exists.
    """
    if folder_id is None:
        return None

    folder = (
        await db.execute(select(Folder).where(Folder.id == folder_id))
    ).scalar_one_or_none()
    if folder is None or folder.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    return folder
