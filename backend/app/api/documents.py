"""Versioned documents: commits, diffs, restore and sharing.

History is append-only. Restoring an old revision appends a new one; nothing is
ever erased or rewritten.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import schemas
from app.core.deps import CurrentUser, DbSession
from app.db.models import (
    Document,
    DocumentCollaborator,
    DocumentComment,
    Project,
    Revision,
    RevisionDiff,
    User,
)
from app.services import audit
from app.services.diff import diff_text
from app.services.documents import empty_document, markdown_to_tiptap, tiptap_to_text
from app.services.permissions import (
    access_expression,
    grant_access,
    require_access,
    revoke_access,
)
from app.services.tree import ensure_default_project, resolve_folder

router = APIRouter(prefix="/documents", tags=["documents"])

Access = Literal["owner", "editor", "viewer"]


# ───────────────────────────── access control ─────────────────────────────


async def resolve_access(
    db: AsyncSession, document_id: uuid.UUID, user: User, required: Access = "viewer"
) -> Access:
    """Authorise against the SQL resolver.

    Ownership and shares are no longer read here: both are expressed as rows
    in access_grants, and the resolver decides. A document the caller cannot
    reach is reported as missing, never as forbidden.
    """
    return await require_access(db, user, "document", document_id, required)


async def _load(db: AsyncSession, document_id: uuid.UUID) -> Document:
    document = (
        await db.execute(
            select(Document)
            .options(
                selectinload(Document.revisions).selectinload(Revision.diff),
                selectinload(Document.collaborators),
            )
            .where(Document.id == document_id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


def _revision_out(revision: Revision) -> schemas.RevisionOut:
    diff = revision.diff
    return schemas.RevisionOut(
        id=revision.id,
        revision_number=revision.revision_number,
        parent_revision_id=revision.parent_revision_id,
        commit_message=revision.commit_message,
        source=revision.source,
        author_email=revision.author_email,
        author_name=revision.author_name,
        created_at=revision.created_at,
        additions=diff.additions if diff else 0,
        deletions=diff.deletions if diff else 0,
        modifications=diff.modifications if diff else 0,
    )


async def _detail(db: AsyncSession, document: Document, access: Access) -> schemas.DocumentDetail:
    owner_email = (
        await db.execute(select(User.email).where(User.id == document.owner_id))
    ).scalar_one_or_none() or ""
    revisions = sorted(document.revisions, key=lambda r: r.revision_number, reverse=True)
    return schemas.DocumentDetail(
        id=document.id,
        title=document.title,
        revision_count=document.revision_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
        access=access,
        owner_email=owner_email,
        project_id=document.project_id,
        folder_id=document.folder_id,
        content_json=document.content_json or empty_document(),
        revisions=[_revision_out(r) for r in revisions],
        collaborators=[
            schemas.CollaboratorOut.model_validate(c) for c in document.collaborators
        ],
    )


async def _commit(
    db: AsyncSession,
    *,
    document: Document,
    content_json: dict,
    message: str,
    author: User,
    source: str,
) -> Revision:
    """Snapshot the content, diff it against the head, append a revision."""
    new_text = tiptap_to_text(content_json)
    delta = diff_text(document.content_text or "", new_text)

    revision = Revision(
        document_id=document.id,
        parent_revision_id=document.head_revision_id,
        revision_number=document.revision_count + 1,
        commit_message=message.strip() or "Update",
        source=source,
        author_id=author.id,
        author_email=author.email,
        author_name=author.full_name or author.email,
        content_json=content_json,
        content_text=new_text,
    )
    db.add(revision)
    await db.flush()

    db.add(
        RevisionDiff(
            revision_id=revision.id,
            blocks=delta["blocks"],
            additions=delta["additions"],
            deletions=delta["deletions"],
            modifications=delta["modifications"],
        )
    )

    document.content_json = content_json
    document.content_text = new_text
    document.revision_count = revision.revision_number
    document.head_revision_id = revision.id
    return revision


# ──────────────────────────────── routes ──────────────────────────────────


@router.get("", response_model=list[schemas.DocumentOut])
async def list_documents(user: CurrentUser, db: DbSession) -> list[schemas.DocumentOut]:
    """Every document the caller can reach, whatever grant reaches it."""
    access = access_expression("document", Document.id, user.id)
    rows = (
        await db.execute(
            select(Document, access.label("access"), User.email)
            .join(User, User.id == Document.owner_id)
            # Index-friendly pre-filter; the resolver is the boundary.
            .where(Document.org_id == user.org_id, access.is_not(None))
            .order_by(Document.updated_at.desc())
        )
    ).all()
    return [
        schemas.DocumentOut(
            id=document.id,
            title=document.title,
            revision_count=document.revision_count,
            created_at=document.created_at,
            updated_at=document.updated_at,
            access=role,
            owner_email=owner_email,
            project_id=document.project_id,
            folder_id=document.folder_id,
        )
        for document, role, owner_email in rows
    ]


async def _new_document(
    db: AsyncSession,
    *,
    user: User,
    title: str,
    project_id: uuid.UUID | None,
    folder_id: uuid.UUID | None,
) -> Document:
    """Place a document in the tree, authorising where it is being placed.

    Editor access to the destination is required — on the folder when one is
    given, otherwise on the project — and the creator becomes the document's
    owner.
    """
    if project_id is None:
        project = await ensure_default_project(db, user)
        project_id = project.id

    folder = await resolve_folder(db, project_id, folder_id)
    if folder is not None:
        await require_access(db, user, "folder", folder.id, "editor")
    else:
        await require_access(db, user, "project", project_id, "editor")

    org_id = (
        await db.execute(select(Project.org_id).where(Project.id == project_id))
    ).scalar_one()

    document = Document(
        owner_id=user.id,
        org_id=org_id,
        project_id=project_id,
        folder_id=folder.id if folder else None,
        title=title,
    )
    db.add(document)
    await db.flush()
    await grant_access(
        db,
        org_id=org_id,
        subject_type="document",
        subject_id=document.id,
        user_id=user.id,
        role="owner",
        granted_by=user.id,
    )
    return document


@router.post("", response_model=schemas.DocumentDetail, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: schemas.DocumentCreate, user: CurrentUser, db: DbSession
) -> schemas.DocumentDetail:
    document = await _new_document(
        db,
        user=user,
        title=payload.title,
        project_id=payload.project_id,
        folder_id=payload.folder_id,
    )
    await _commit(
        db,
        document=document,
        content_json=payload.content_json or empty_document(),
        message="Document created",
        author=user,
        source="human",
    )
    await audit.record_audit(
        db,
        org_id=document.org_id,
        actor_id=user.id,
        action=audit.DOCUMENT_CREATE,
        subject_type="document",
        subject_id=document.id,
        payload={"title": document.title, "source": "human"},
    )
    await db.commit()
    return await _detail(db, await _load(db, document.id), "owner")


@router.post(
    "/from-markdown", response_model=schemas.DocumentDetail, status_code=status.HTTP_201_CREATED
)
async def create_from_markdown(
    payload: schemas.DocumentFromMarkdown, user: CurrentUser, db: DbSession
) -> schemas.DocumentDetail:
    """The agent's way into version control — first revision is source='agent'."""
    document = await _new_document(
        db,
        user=user,
        title=payload.title,
        project_id=payload.project_id,
        folder_id=payload.folder_id,
    )
    await _commit(
        db,
        document=document,
        content_json=markdown_to_tiptap(payload.markdown),
        message=payload.commit_message,
        author=user,
        source="agent",
    )
    await audit.record_audit(
        db,
        org_id=document.org_id,
        actor_id=user.id,
        action=audit.DOCUMENT_CREATE,
        subject_type="document",
        subject_id=document.id,
        payload={"title": document.title, "source": "agent"},
    )
    await db.commit()
    return await _detail(db, await _load(db, document.id), "owner")


@router.get("/{document_id}", response_model=schemas.DocumentDetail)
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> schemas.DocumentDetail:
    access = await resolve_access(db, document_id, user, "viewer")
    return await _detail(db, await _load(db, document_id), access)


@router.patch("/{document_id}", response_model=schemas.DocumentOut)
async def rename_document(
    document_id: uuid.UUID, payload: schemas.TitleUpdate, user: CurrentUser, db: DbSession
) -> Document:
    await resolve_access(db, document_id, user, "editor")
    document = await _load(db, document_id)
    document.title = payload.title
    await db.commit()
    await db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_document(document_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await resolve_access(db, document_id, user, "owner")
    document = await _load(db, document_id)
    await audit.record_audit(
        db,
        org_id=document.org_id,
        actor_id=user.id,
        action=audit.DOCUMENT_DELETE,
        subject_type="document",
        subject_id=document.id,
        payload={"title": document.title, "revision_count": document.revision_count},
    )
    await db.delete(document)
    await db.commit()


@router.post("/{document_id}/commits", response_model=schemas.DocumentDetail)
async def commit(
    document_id: uuid.UUID, payload: schemas.CommitCreate, user: CurrentUser, db: DbSession
) -> schemas.DocumentDetail:
    access = await resolve_access(db, document_id, user, "editor")
    document = await _load(db, document_id)

    revision = await _commit(
        db,
        document=document,
        content_json=payload.content_json,
        message=payload.commit_message,
        author=user,
        source="human",
    )
    await audit.record_audit(
        db,
        org_id=document.org_id,
        actor_id=user.id,
        action=audit.DOCUMENT_COMMIT,
        subject_type="document",
        subject_id=document.id,
        payload={"revision_number": revision.revision_number, "message": revision.commit_message},
    )
    await db.commit()
    return await _detail(db, await _load(db, document_id), access)


@router.get("/{document_id}/revisions/{revision_id}/diff", response_model=schemas.DiffOut)
async def get_diff(
    document_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> schemas.DiffOut:
    await resolve_access(db, document_id, user, "viewer")
    document = await _load(db, document_id)
    revision = next((r for r in document.revisions if r.id == revision_id), None)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Revision not found")
    diff = revision.diff
    if diff is None:
        return schemas.DiffOut()
    return schemas.DiffOut(
        additions=diff.additions,
        deletions=diff.deletions,
        modifications=diff.modifications,
        blocks=[schemas.DiffBlock(**b) for b in diff.blocks],
    )


@router.post("/{document_id}/revisions/{revision_id}/restore", response_model=schemas.DocumentDetail)
async def restore(
    document_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> schemas.DocumentDetail:
    """Restoring adds a commit. Nothing is ever erased."""
    access = await resolve_access(db, document_id, user, "editor")
    document = await _load(db, document_id)

    revision = next((r for r in document.revisions if r.id == revision_id), None)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Revision not found")

    restored = await _commit(
        db,
        document=document,
        content_json=revision.content_json,
        message=f"Restore revision {revision.revision_number}",
        author=user,
        source="human",
    )
    await audit.record_audit(
        db,
        org_id=document.org_id,
        actor_id=user.id,
        action=audit.DOCUMENT_RESTORE,
        subject_type="document",
        subject_id=document.id,
        payload={
            "restored_revision_number": revision.revision_number,
            "new_revision_number": restored.revision_number,
        },
    )
    await db.commit()
    return await _detail(db, await _load(db, document_id), access)


@router.post(
    "/{document_id}/collaborators",
    response_model=schemas.CollaboratorOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_collaborator(
    document_id: uuid.UUID,
    payload: schemas.CollaboratorCreate,
    user: CurrentUser,
    db: DbSession,
) -> DocumentCollaborator:
    """Mocked invitation: no mail is sent and no OTP is issued.

    An existing account is linked immediately; anyone else is stored as pending
    and claimed when they register.
    """
    await resolve_access(db, document_id, user, "owner")
    document = await _load(db, document_id)

    email = payload.email.lower()
    if email == user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You already own this document")
    if any(c.email == email for c in document.collaborators):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already shared with that address")

    invitee = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    share = DocumentCollaborator(
        document_id=document.id,
        email=email,
        role=payload.role,
        user_id=invitee.id if invitee else None,
        invite_status="accepted" if invitee else "pending",
    )
    db.add(share)

    # A share is only access once it is a grant — the resolver reads grants,
    # not this table. Pending invitations have no account to grant to yet, and
    # an invitee from another organisation cannot be granted anything: the
    # boundary is absolute, and a grant written across it would be inert.
    if invitee is not None and invitee.org_id == document.org_id:
        await grant_access(
            db,
            org_id=document.org_id,
            subject_type="document",
            subject_id=document.id,
            user_id=invitee.id,
            role=payload.role,
            granted_by=user.id,
        )

    await audit.record_audit(
        db,
        org_id=document.org_id,
        actor_id=user.id,
        action=audit.SHARE_GRANT,
        subject_type="document",
        subject_id=document.id,
        payload={
            "email": email,
            "role": payload.role,
            "granted": bool(invitee is not None and invitee.org_id == document.org_id),
        },
    )
    await db.commit()
    await db.refresh(share)
    return share


@router.delete(
    "/{document_id}/collaborators/{collaborator_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def remove_collaborator(
    document_id: uuid.UUID, collaborator_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    await resolve_access(db, document_id, user, "owner")
    document = await _load(db, document_id)
    share = next((c for c in document.collaborators if c.id == collaborator_id), None)
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collaborator not found")
    if share.user_id is not None:
        await revoke_access(
            db, subject_type="document", subject_id=document.id, user_id=share.user_id
        )
    await audit.record_audit(
        db,
        org_id=document.org_id,
        actor_id=user.id,
        action=audit.SHARE_REVOKE,
        subject_type="document",
        subject_id=document.id,
        payload={"email": share.email, "role": share.role},
    )
    await db.delete(share)
    await db.commit()


# ─────────────────────────────── comments (E6) ────────────────────────────────
#
# A comment is a body + display metadata anchored to a TipTap ``comment`` mark
# in the document. The mark carries a client-generated ``markId`` and lives in
# ``documents.content_json`` (durable at commit time); the bodies live in the
# ``document_comments`` table (durable immediately). See
# ``0005_document_comments.py`` for the schema and ``commentMark.ts`` in the
# frontend for the mark itself.
#
# Access:
#   viewer  — GET (list a document's comments)
#   editor  — POST (add), POST /resolve (mark all comments in a thread resolved)
#   author  — DELETE a thread they wrote every comment of
#   owner   — DELETE any thread
#
# Envelope shapes match v1 so the frontend port reuses its serialisers: GET
# returns ``{"items": [...]}`` and resolve returns ``{"comments": [...]}``.


def _comment_out(comment: DocumentComment) -> schemas.CommentOut:
    return schemas.CommentOut(
        id=comment.id,
        document_id=comment.document_id,
        mark_id=comment.mark_id,
        body=comment.body,
        quoted_text=comment.quoted_text,
        author_id=comment.author_id,
        author_email=comment.author_email,
        author_name=comment.author_name,
        resolved_at=comment.resolved_at,
        created_at=comment.created_at,
    )


async def _load_document_row(db: AsyncSession, document_id: uuid.UUID) -> Document:
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


async def _load_thread(
    db: AsyncSession, document_id: uuid.UUID, mark_id: str
) -> list[DocumentComment]:
    result = await db.execute(
        select(DocumentComment)
        .where(
            DocumentComment.document_id == document_id,
            DocumentComment.mark_id == mark_id,
        )
        .order_by(DocumentComment.created_at)
    )
    return list(result.scalars().all())


@router.get("/{document_id}/comments", response_model=schemas.CommentList)
async def list_comments(
    document_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> schemas.CommentList:
    """Every comment on the document, oldest first. Viewer-and-up."""
    await resolve_access(db, document_id, user, "viewer")
    result = await db.execute(
        select(DocumentComment)
        .where(DocumentComment.document_id == document_id)
        .order_by(DocumentComment.created_at)
    )
    return schemas.CommentList(items=[_comment_out(c) for c in result.scalars().all()])


@router.post(
    "/{document_id}/comments",
    response_model=schemas.CommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    document_id: uuid.UUID,
    payload: schemas.CommentCreate,
    user: CurrentUser,
    db: DbSession,
) -> schemas.CommentOut:
    """Editor-and-up. The client applies the mark to the editor only AFTER
    this POST succeeds, so a failed create never leaves an orphan highlight."""
    await resolve_access(db, document_id, user, "editor")
    document = await _load_document_row(db, document_id)

    comment = DocumentComment(
        document_id=document.id,
        org_id=document.org_id,
        project_id=document.project_id,
        mark_id=payload.mark_id,
        body=payload.body,
        quoted_text=payload.quoted_text,
        author_id=user.id,
        author_email=user.email,
        author_name=user.full_name or user.email,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return _comment_out(comment)


@router.post(
    "/{document_id}/comments/{mark_id}/resolve",
    response_model=schemas.CommentResolveResponse,
)
async def resolve_comment_thread(
    document_id: uuid.UUID,
    mark_id: str,
    user: CurrentUser,
    db: DbSession,
) -> schemas.CommentResolveResponse:
    """Mark every comment in the thread resolved (idempotent — already-resolved
    rows are left alone). Editor-and-up.

    The highlight itself stays on the document — the client may render a
    resolved thread differently, but the range remains navigable so someone
    can reopen the discussion at any time.
    """
    await resolve_access(db, document_id, user, "editor")
    thread = await _load_thread(db, document_id, mark_id)
    if not thread:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment thread not found")

    now = datetime.now(timezone.utc)
    for comment in thread:
        if comment.resolved_at is None:
            comment.resolved_at = now
            comment.resolved_by_id = user.id
    await db.commit()
    for comment in thread:
        await db.refresh(comment)
    return schemas.CommentResolveResponse(
        comments=[_comment_out(c) for c in thread]
    )


@router.delete(
    "/{document_id}/comments/{mark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_comment_thread(
    document_id: uuid.UUID,
    mark_id: str,
    user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete the whole thread. An owner may delete anyone's; a non-owner
    editor may only delete a thread they authored every comment of."""
    access = await resolve_access(db, document_id, user, "editor")
    thread = await _load_thread(db, document_id, mark_id)
    if not thread:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment thread not found")

    if access != "owner":
        if any(c.author_id != user.id for c in thread):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the thread's author or the document owner can delete this thread",
            )

    for comment in thread:
        await db.delete(comment)
    await db.commit()
