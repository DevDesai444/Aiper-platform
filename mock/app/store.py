"""In-memory state. Dicts where the real service has Postgres and Qdrant.

Everything structural is real: access control, the revision graph, block diffs,
page-level retrieval. Only the storage medium and the embeddings are simulated.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.diff import diff_text
from app.documents import empty_document, tiptap_to_text

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "is", "are", "be",
    "shall", "with", "on", "at", "by", "this", "that", "it", "as", "from",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def _terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS and len(t) > 2]


# ──────────────────────────────── records ─────────────────────────────────


@dataclass
class User:
    id: uuid.UUID
    email: str
    full_name: str
    organisation: str
    hashed_password: str
    created_at: datetime = field(default_factory=now)


@dataclass
class PageChunk:
    file_id: uuid.UUID
    filename: str
    page: int
    text: str
    terms: Counter = field(default_factory=Counter)


@dataclass
class FileAsset:
    id: uuid.UUID
    owner_id: uuid.UUID
    filename: str
    extension: str
    size_bytes: int
    comparison_role: str = "source"
    session_id: uuid.UUID | None = None
    page_count: int = 0
    indexed: bool = False
    index_error: str | None = None
    storage_path: str = ""
    created_at: datetime = field(default_factory=now)


@dataclass
class Template:
    id: uuid.UUID
    key: str
    name: str
    standard: str
    description: str
    sections: list[dict[str, Any]]
    is_builtin: bool = False
    owner_id: uuid.UUID | None = None


@dataclass
class Message:
    id: uuid.UUID
    role: str
    content: str
    mode: str | None
    activity: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=now)


@dataclass
class Session:
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    mode: str
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass
class Revision:
    id: uuid.UUID
    document_id: uuid.UUID
    parent_revision_id: uuid.UUID | None
    revision_number: int
    commit_message: str
    source: str
    author_email: str
    author_name: str
    content_json: dict[str, Any]
    content_text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    modifications: int = 0
    created_at: datetime = field(default_factory=now)


@dataclass
class Collaborator:
    id: uuid.UUID
    email: str
    role: str
    invite_status: str
    user_id: uuid.UUID | None = None
    created_at: datetime = field(default_factory=now)


@dataclass
class Document:
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    content_json: dict[str, Any] = field(default_factory=empty_document)
    content_text: str = ""
    revision_count: int = 0
    head_revision_id: uuid.UUID | None = None
    revisions: list[Revision] = field(default_factory=list)
    collaborators: list[Collaborator] = field(default_factory=list)
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


# ───────────────────────────────── state ──────────────────────────────────


class Store:
    def __init__(self) -> None:
        self.users: dict[uuid.UUID, User] = {}
        self.files: dict[uuid.UUID, FileAsset] = {}
        self.pages: list[PageChunk] = []
        self.templates: dict[uuid.UUID, Template] = {}
        self.sessions: dict[uuid.UUID, Session] = {}
        self.documents: dict[uuid.UUID, Document] = {}

    # identity ------------------------------------------------------------
    def user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email.lower()), None)

    def add_user(self, user: User) -> User:
        self.users[user.id] = user
        # Claim shares addressed to this email before the account existed.
        for document in self.documents.values():
            for share in document.collaborators:
                if share.email == user.email and share.user_id is None:
                    share.user_id = user.id
                    share.invite_status = "accepted"
        return user

    # retrieval -----------------------------------------------------------
    def index_pages(self, asset: FileAsset, pages: list[Any]) -> int:
        self.pages = [p for p in self.pages if p.file_id != asset.id]
        for page in pages:
            self.pages.append(
                PageChunk(
                    file_id=asset.id,
                    filename=asset.filename,
                    page=page.page,
                    text=page.text,
                    terms=Counter(_terms(page.text)),
                )
            )
        return len(pages)

    def search(
        self,
        *,
        owner_id: uuid.UUID,
        query: str,
        limit: int = 6,
        file_ids: list[uuid.UUID] | None = None,
    ) -> list[tuple[PageChunk, float]]:
        """Lexical scoring stands in for embeddings. The page numbers are real."""
        wanted = _terms(query)
        if not wanted:
            return []

        candidates = [
            p
            for p in self.pages
            if self.files.get(p.file_id)
            and self.files[p.file_id].owner_id == owner_id
            and (file_ids is None or p.file_id in file_ids)
        ]
        if not candidates:
            return []

        document_frequency = Counter()
        for page in candidates:
            for term in set(wanted):
                if page.terms.get(term):
                    document_frequency[term] += 1

        scored: list[tuple[PageChunk, float]] = []
        total = len(candidates)
        for page in candidates:
            length = max(1, sum(page.terms.values()))
            score = 0.0
            for term in wanted:
                count = page.terms.get(term, 0)
                if not count:
                    continue
                idf = math.log(1 + total / (1 + document_frequency[term]))
                score += (count / length) * idf * 100
            if score > 0:
                scored.append((page, round(min(score / 4, 1.0), 3)))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def file_pages(self, file_id: uuid.UUID) -> list[PageChunk]:
        return sorted((p for p in self.pages if p.file_id == file_id), key=lambda p: p.page)

    def drop_file(self, file_id: uuid.UUID) -> None:
        self.files.pop(file_id, None)
        self.pages = [p for p in self.pages if p.file_id != file_id]

    # documents -----------------------------------------------------------
    def commit(
        self,
        document: Document,
        *,
        content_json: dict[str, Any],
        message: str,
        author: User,
        source: str,
    ) -> Revision:
        new_text = tiptap_to_text(content_json)
        delta = diff_text(document.content_text or "", new_text)

        revision = Revision(
            id=new_id(),
            document_id=document.id,
            parent_revision_id=document.head_revision_id,
            revision_number=document.revision_count + 1,
            commit_message=message.strip() or "Update",
            source=source,
            author_email=author.email,
            author_name=author.full_name or author.email,
            content_json=content_json,
            content_text=new_text,
            blocks=delta["blocks"],
            additions=delta["additions"],
            deletions=delta["deletions"],
            modifications=delta["modifications"],
        )
        document.revisions.append(revision)
        document.content_json = content_json
        document.content_text = new_text
        document.revision_count = revision.revision_number
        document.head_revision_id = revision.id
        document.updated_at = now()
        return revision

    def access(self, document: Document, user: User) -> str | None:
        if document.owner_id == user.id:
            return "owner"
        share = next((c for c in document.collaborators if c.email == user.email), None)
        if share is None:
            return None
        return "viewer" if share.role == "viewer" else "editor"


store = Store()
