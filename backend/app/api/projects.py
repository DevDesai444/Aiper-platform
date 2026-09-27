"""Projects and folders: the tree documents hang from.

Every read and write here goes through app.services.permissions, which goes
through the SQL resolver. No route reimplements the cascade.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import schemas
from app.core.deps import CurrentUser, DbSession
from app.db.models import AccessGrant, Document, Folder, Project, User
from app.services import audit
from app.services.permissions import (
    access_expression,
    effective_access,
    grant_access,
    has_access,
    require_access,
    revoke_access,
)
from app.services.refs import resolve_ref
from app.services.tree import resolve_folder, would_cycle

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


@router.patch("/{project_ref}", response_model=schemas.ProjectOut)
async def rename_project(
    project_ref: str, payload: schemas.ProjectRename, user: CurrentUser, db: DbSession
) -> schemas.ProjectOut:
    """Rename a project. Editor and above — the same threshold projects UPDATE
    has enforced at the row-policy layer since row-level security landed."""
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    role = await require_access(db, user, "project", project_id, "editor")
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    project.name = payload.name
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.PROJECT_RENAME,
        subject_type="project",
        subject_id=project.id,
        payload={"name": project.name},
    )
    await db.commit()
    await db.refresh(project)
    return _project_out(project, role)


@router.delete("/{project_ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_ref: str, user: CurrentUser, db: DbSession) -> None:
    """Delete a project and everything under it. Owner only.

    Folders and documents cascade at the database layer; file assets survive
    with their project link cleared (an upload is the uploader's, not the
    project's). The audit event is written before the row goes, because
    evidence of a deletion must not depend on the deleted row.
    """
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "project", project_id, "owner")
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.PROJECT_DELETE,
        subject_type="project",
        subject_id=project.id,
        payload={"name": project.name},
    )
    await db.delete(project)
    await db.commit()


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
    "/{project_ref}/folders",
    response_model=schemas.FolderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    project_ref: str,
    payload: schemas.FolderCreate,
    user: CurrentUser,
    db: DbSession,
) -> Folder:
    """Add a folder to a project, optionally nested under another."""
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "project", project_id, "editor")
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


@router.patch("/{project_ref}/folders/{folder_id}", response_model=schemas.FolderOut)
async def rename_folder(
    project_ref: str,
    folder_id: uuid.UUID,
    payload: schemas.FolderRename,
    user: CurrentUser,
    db: DbSession,
) -> Folder:
    """Rename a folder in place. Editor and above, matching folders UPDATE."""
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "folder", folder_id, "editor")
    folder = (
        await db.execute(select(Folder).where(Folder.id == folder_id))
    ).scalar_one_or_none()
    if folder is None or folder.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    folder.name = payload.name
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.FOLDER_RENAME,
        subject_type="folder",
        subject_id=folder.id,
        payload={"name": folder.name},
    )
    await db.commit()
    await db.refresh(folder)
    return folder


@router.patch("/{project_ref}/folders/{folder_id}/move", response_model=schemas.FolderOut)
async def move_folder(
    project_ref: str,
    folder_id: uuid.UUID,
    payload: schemas.FolderMove,
    user: CurrentUser,
    db: DbSession,
) -> Folder:
    """Move a folder to a new parent within the same project.

    Three checks, matching the relocation guard trigger underneath: editor
    access on the folder being moved, the destination is a real folder in this
    project (or the project root), and editor access on that destination. The
    fourth is one the trigger cannot make: the destination must not be the
    folder itself or anywhere inside its own subtree, or the move would make a
    folder its own ancestor.
    """
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "folder", folder_id, "editor")
    folder = (
        await db.execute(select(Folder).where(Folder.id == folder_id))
    ).scalar_one_or_none()
    if folder is None or folder.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    destination = await resolve_folder(db, project_id, payload.parent_folder_id)
    if destination is not None:
        if destination.id == folder.id or await would_cycle(db, folder.id, destination.id):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "A folder cannot be moved into itself"
            )
        await require_access(db, user, "folder", destination.id, "editor")
    else:
        await require_access(db, user, "project", project_id, "editor")

    folder.parent_folder_id = destination.id if destination else None
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.FOLDER_MOVE,
        subject_type="folder",
        subject_id=folder.id,
        payload={"parent_folder_id": str(destination.id) if destination else None},
    )
    await db.commit()
    await db.refresh(folder)
    return folder


@router.delete("/{project_ref}/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    project_ref: str, folder_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    """Delete a folder and its subfolders. Owner only, matching folders DELETE.

    Documents anywhere in the deleted subtree are not destroyed: folder_id is
    ON DELETE SET NULL, so each one surfaces at the project root as its
    containing folder disappears from under it.
    """
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "folder", folder_id, "owner")
    folder = (
        await db.execute(select(Folder).where(Folder.id == folder_id))
    ).scalar_one_or_none()
    if folder is None or folder.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.FOLDER_DELETE,
        subject_type="folder",
        subject_id=folder.id,
        payload={"name": folder.name},
    )
    await db.delete(folder)
    await db.commit()


@router.get("/{project_ref}/tree", response_model=schemas.ProjectTree)
async def project_tree(
    project_ref: str, user: CurrentUser, db: DbSession
) -> schemas.ProjectTree:
    """The folders and documents of this project that the caller can see.

    `project_ref` is a full uuid or the 8-hex id a `/projects/{slug}-{id8}`
    URL carries; `resolve_ref` is a lookup, not the authorisation boundary — a
    ref that resolves to a real row still has to clear the checks below.

    Deliberately not gated on project-level access: somebody granted a single
    folder can still enumerate that folder's contents. `project` is omitted
    unless they can see the project itself, and a caller who can see nothing
    at all gets 404 rather than an empty tree, so the response never confirms
    that a project exists.
    """
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

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


# ─────────────────────────────────── members ───────────────────────────────
#
# access_grants at project scope, exposed directly — unlike document sharing,
# there is no separate "pending invite by email" table here: the brief wants
# a clean 404 for an email that does not resolve to a real user in the
# caller's own org, not an inert row waiting for someone who may never
# register. grant_access/revoke_access (services/permissions.py) already do
# everything a route needs; access_grants already carries full privileges for
# aiper_app (migration 0003), so this needs no new grant migration.


def _member_out(grant: AccessGrant) -> schemas.ProjectMemberOut:
    return schemas.ProjectMemberOut(
        user_id=grant.user_id,
        email=grant.user.email,
        full_name=grant.user.full_name,
        role=grant.role,
        created_at=grant.created_at,
    )


@router.get("/{project_ref}/members", response_model=list[schemas.ProjectMemberOut])
async def list_members(
    project_ref: str, user: CurrentUser, db: DbSession
) -> list[schemas.ProjectMemberOut]:
    """Everyone with a direct grant on this project, owners included."""
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "project", project_id, "viewer")

    grants = (
        (
            await db.execute(
                select(AccessGrant)
                .options(selectinload(AccessGrant.user))
                .where(AccessGrant.subject_type == "project", AccessGrant.subject_id == project_id)
                .order_by(AccessGrant.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_member_out(grant) for grant in grants]


@router.post(
    "/{project_ref}/members",
    response_model=schemas.ProjectMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    project_ref: str,
    payload: schemas.ProjectMemberCreate,
    user: CurrentUser,
    db: DbSession,
) -> schemas.ProjectMemberOut:
    """Grant a member editor or viewer access. Owner only, matching delete_project's threshold."""
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "project", project_id, "owner")

    email = payload.email.lower()
    # Same org only: the resolver would refuse a cross-org grant anyway, but
    # a clean 404 here beats writing a row that could never authorise anyone.
    invitee = (
        await db.execute(
            select(User).where(User.email == email, User.org_id == user.org_id)
        )
    ).scalar_one_or_none()
    if invitee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No one with that address in your organisation")

    await grant_access(
        db,
        org_id=user.org_id,
        subject_type="project",
        subject_id=project_id,
        user_id=invitee.id,
        role=payload.role,
        granted_by=user.id,
    )
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.SHARE_GRANT,
        subject_type="project",
        subject_id=project_id,
        payload={"email": email, "role": payload.role},
    )
    await db.commit()
    grant = (
        await db.execute(
            select(AccessGrant)
            .options(selectinload(AccessGrant.user))
            .where(
                AccessGrant.subject_type == "project",
                AccessGrant.subject_id == project_id,
                AccessGrant.user_id == invitee.id,
            )
        )
    ).scalar_one()
    return _member_out(grant)


@router.delete("/{project_ref}/members/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_ref: str, target_user_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    """Revoke a member's access. Owner only; the last owner cannot be removed."""
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "project", project_id, "owner")

    target = (
        await db.execute(
            select(AccessGrant).where(
                AccessGrant.subject_type == "project",
                AccessGrant.subject_id == project_id,
                AccessGrant.user_id == target_user_id,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")

    if target.role == "owner":
        owner_count = (
            await db.execute(
                select(func.count()).where(
                    AccessGrant.subject_type == "project",
                    AccessGrant.subject_id == project_id,
                    AccessGrant.role == "owner",
                )
            )
        ).scalar_one()
        if owner_count <= 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A project must keep at least one owner"
            )

    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.SHARE_REVOKE,
        subject_type="project",
        subject_id=project_id,
        payload={"user_id": str(target_user_id), "role": target.role},
    )
    await revoke_access(db, subject_type="project", subject_id=project_id, user_id=target_user_id)
    await db.commit()
