"""Projects and folders: the tree documents hang from.

Every read and write here goes through app.services.permissions, which goes
through the SQL resolver. No route reimplements the cascade.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app import schemas
from app.core.deps import CurrentUser, DbSession
from app.db.models import Document, Folder, Project
from app.services import audit
from app.services.permissions import (
    access_expression,
    effective_access,
    grant_access,
    has_access,
    requires,
)
from app.services.tree import resolve_folder

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_out(project: Project, access: str) -> schemas.ProjectOut:
    return schemas.ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        access=access,
    )


@router.post("", response_model=schemas.ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: schemas.ProjectCreate, user: CurrentUser, db: DbSession
) -> schemas.ProjectOut:
    """Create a project in the caller's organisation; the creator owns it."""
    project = Project(
        org_id=user.org_id,
        name=payload.name,
        description=payload.description,
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
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.PROJECT_CREATE,
        subject_type="project",
        subject_id=project.id,
        payload={"name": project.name},
    )
    await db.commit()
    await db.refresh(project)
    return _project_out(project, "owner")


@router.get("", response_model=list[schemas.ProjectOut])
async def list_projects(user: CurrentUser, db: DbSession) -> list[schemas.ProjectOut]:
    """Every project the caller has any effective access to."""
    access = access_expression("project", Project.id, user.id)
    rows = (
        await db.execute(
            select(Project, access.label("access"))
            # The organisation predicate is an index-friendly pre-filter, not
            # the boundary: the resolver enforces that regardless.
            .where(Project.org_id == user.org_id, access.is_not(None))
            .order_by(Project.created_at.desc())
        )
    ).all()
    return [_project_out(project, role) for project, role in rows]


@router.post(
    "/{project_id}/folders",
    response_model=schemas.FolderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    project_id: uuid.UUID,
    payload: schemas.FolderCreate,
    user: CurrentUser,
    db: DbSession,
    _: str = Depends(requires("project", "editor")),
) -> Folder:
    """Add a folder to a project, optionally nested under another."""
    parent = await resolve_folder(db, project_id, payload.parent_folder_id)

    folder = Folder(
        project_id=project_id,
        parent_folder_id=parent.id if parent else None,
        name=payload.name,
    )
    db.add(folder)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.FOLDER_CREATE,
        subject_type="folder",
        subject_id=folder.id,
        payload={"name": folder.name, "project_id": str(project_id)},
    )
    await db.commit()
    await db.refresh(folder)
    return folder


@router.get("/{project_id}/tree", response_model=schemas.ProjectTree)
async def project_tree(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> schemas.ProjectTree:
    """The folders and documents of this project that the caller can see.

    Deliberately not gated on project-level access: somebody granted a single
    folder can still enumerate that folder's contents. `project` is omitted
    unless they can see the project itself, and a caller who can see nothing
    at all gets 404 rather than an empty tree, so the response never confirms
    that a project exists.
    """
    project_role = await effective_access(db, user.id, "project", project_id)

    folders = (
        (
            await db.execute(
                select(Folder)
                .where(
                    Folder.project_id == project_id,
                    has_access("folder", Folder.id, user.id),
                )
                .order_by(Folder.name)
            )
        )
        .scalars()
        .all()
    )
    documents = (
        (
            await db.execute(
                select(Document)
                .where(
                    Document.project_id == project_id,
                    has_access("document", Document.id, user.id),
                )
                .order_by(Document.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )

    if project_role is None and not folders and not documents:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    project = None
    if project_role is not None:
        row = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if row is not None:
            project = _project_out(row, project_role)

    return schemas.ProjectTree(
        project=project,
        folders=[schemas.FolderOut.model_validate(f) for f in folders],
        documents=[schemas.TreeDocument.model_validate(d) for d in documents],
    )
