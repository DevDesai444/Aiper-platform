"""Builders for the tenancy tree, so the tests read as scenarios."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccessGrant, Document, Folder, Organisation, Project, User


async def make_org(db: AsyncSession, slug: str) -> Organisation:
    org = Organisation(name=slug.title(), slug=slug)
    db.add(org)
    await db.flush()
    return org


async def make_user(db: AsyncSession, org: Organisation, email: str) -> User:
    user = User(
        email=email,
        org_id=org.id,
        full_name=email.split("@")[0],
        organisation=org.name,
        hashed_password="not-a-real-hash",
    )
    db.add(user)
    await db.flush()
    return user


async def make_project(
    db: AsyncSession, org: Organisation, creator: User, name: str = "Project"
) -> Project:
    project = Project(org_id=org.id, name=name, description="", created_by=creator.id)
    db.add(project)
    await db.flush()
    return project


async def make_folder(
    db: AsyncSession,
    project: Project,
    name: str = "Folder",
    parent: Folder | None = None,
) -> Folder:
    folder = Folder(
        project_id=project.id,
        parent_folder_id=parent.id if parent else None,
        name=name,
    )
    db.add(folder)
    await db.flush()
    return folder


async def make_document(
    db: AsyncSession,
    owner: User,
    project: Project,
    folder: Folder | None = None,
    title: str = "Document",
) -> Document:
    document = Document(
        owner_id=owner.id,
        org_id=project.org_id,
        project_id=project.id,
        folder_id=folder.id if folder else None,
        title=title,
    )
    db.add(document)
    await db.flush()
    return document


async def make_grant(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    subject_type: str,
    subject_id: uuid.UUID,
    user: User,
    role: str,
) -> AccessGrant:
    """Insert a grant row directly, bypassing the service layer.

    Tests that forge grants need exactly this: the resolver must hold the line
    on rows nothing in the application would ever write.
    """
    grant = AccessGrant(
        org_id=org_id,
        subject_type=subject_type,
        subject_id=subject_id,
        user_id=user.id,
        role=role,
    )
    db.add(grant)
    await db.flush()
    return grant
