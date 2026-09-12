"""Relational model: accounts, sources, conversations and the revision graph."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


# ──────────────────────────────── identity ────────────────────────────────


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    organisation: Mapped[str] = mapped_column(String(160), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ──────────────────────────────── sources ─────────────────────────────────


class FileAsset(Base, TimestampMixin):
    """An uploaded source document. One row per file; pages live in Qdrant."""

    __tablename__ = "file_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
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
