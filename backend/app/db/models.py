"""Relational model: tenancy, accounts, sources, conversations and the revision graph."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


# ──────────────────────────────── tenancy ─────────────────────────────────
#
# organisation -> project -> folder (nested) -> document.
#
# Access is granted per subject and cascades downward; the organisation
# boundary is absolute. Nothing here decides access: the one authority is the
# SQL function aiper_effective_access (migration 0002), called through
# app.services.permissions.

SubjectType = Literal["project", "folder", "document"]
Role = Literal["owner", "editor", "viewer"]

# create_type=False: the types are created by migration 0002, not by the mapper.
subject_enum = ENUM("project", "folder", "document", name="aiper_subject", create_type=False)
role_enum = ENUM("owner", "editor", "viewer", name="aiper_role", create_type=False)


class Organisation(Base, TimestampMixin):
    """A tenant. Users of one organisation share a database, nothing else."""

    __tablename__ = "organisations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160), unique=True)

    users: Mapped[list[User]] = relationship(back_populates="org")
    projects: Mapped[list[Project]] = relationship(back_populates="org")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, server_default="", default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    org: Mapped[Organisation] = relationship(back_populates="projects")
    folders: Mapped[list[Folder]] = relationship(back_populates="project")


class Folder(Base, TimestampMixin):
    """A node in a project's tree. Deleting one deletes the subtree below it."""

    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # That a parent belongs to the same project as its child is a service-layer
    # invariant (app.services.tree); the resolver additionally refuses to walk
    # out of the project, so a bad parent cannot carry access across the tree.
    parent_folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))

    project: Mapped[Project] = relationship(back_populates="folders")
    children: Mapped[list[Folder]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped[Folder | None] = relationship(
        back_populates="children", remote_side="Folder.id"
    )


class AccessGrant(Base):
    """One subject, one user, one role. The unit the resolver reads.

    Polymorphic on purpose: one table for three subject kinds keeps the
    resolver a single query. subject_id therefore carries no foreign key, so
    deleting a subject leaves an inert row behind — the resolver joins through
    the real tables, and an orphan grants nothing.
    """

    __tablename__ = "access_grants"
    __table_args__ = (
        UniqueConstraint(
            "subject_type", "subject_id", "user_id", name="uq_access_grants_subject_user"
        ),
        Index("ix_access_grants_subject", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id", ondelete="CASCADE"))
    subject_type: Mapped[SubjectType] = mapped_column(subject_enum)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(role_enum)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


# ──────────────────────────────── identity ────────────────────────────────


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    # The tenant. `organisation` below is the legacy free-text string kept for
    # the registration payload and /auth/me; org_id is what access keys off.
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"), index=True)
    organisation: Mapped[str] = mapped_column(String(160), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    org: Mapped[Organisation] = relationship(back_populates="users")


# ──────────────────────────────── sources ─────────────────────────────────


class FileAsset(Base, TimestampMixin):
    """An uploaded source document. One row per file; pages live in Qdrant."""

    __tablename__ = "file_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(160), default="")
    extension: Mapped[str] = mapped_column(String(16), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(1024), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    comparison_role: Mapped[str] = mapped_column(String(16), default="source")  # source | target


class DocumentTemplate(Base, TimestampMixin):
    """A structured skeleton the document-generation skill fills in."""

    __tablename__ = "document_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(200))
    standard: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)


# ───────────────────────────── conversations ──────────────────────────────


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="New conversation")
    mode: Mapped[str] = mapped_column(String(32), default="document_generation")

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The agent activity feed, replayed verbatim when a conversation is reopened.
    activity: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


# ────────────────────────── versioned documents ───────────────────────────


class Document(Base, TimestampMixin):
    """The head of a document. Every accepted change is a Revision."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # NULL means the project root. A deleted folder leaves its documents there
    # rather than destroying them.
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="Untitled document")
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    content_text: Mapped[str] = mapped_column(Text, default="")
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    head_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    revisions: Mapped[list[Revision]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="Revision.revision_number.desc()",
    )
    collaborators: Mapped[list[DocumentCollaborator]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Revision(Base):
    """An immutable commit. History is appended to, never rewritten."""

    __tablename__ = "revisions"
    __table_args__ = (UniqueConstraint("document_id", "revision_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    commit_message: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(16), default="human")  # human | agent
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_email: Mapped[str] = mapped_column(String(320), default="")
    author_name: Mapped[str] = mapped_column(String(160), default="")
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    content_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[Document] = relationship(back_populates="revisions")
    diff: Mapped[RevisionDiff | None] = relationship(
        back_populates="revision", cascade="all, delete-orphan", uselist=False
    )


class RevisionDiff(Base):
    """The materialised block diff of a revision against its parent."""

    __tablename__ = "revision_diffs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revisions.id", ondelete="CASCADE"), unique=True, index=True
    )
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    modifications: Mapped[int] = mapped_column(Integer, default=0)

    revision: Mapped[Revision] = relationship(back_populates="diff")


class DocumentCollaborator(Base, TimestampMixin):
    """A share. Invitations are mocked: no mail is sent, no OTP is issued."""

    __tablename__ = "document_collaborators"
    __table_args__ = (UniqueConstraint("document_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), default="editor")  # editor | viewer
    invite_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | accepted

    document: Mapped[Document] = relationship(back_populates="collaborators")
