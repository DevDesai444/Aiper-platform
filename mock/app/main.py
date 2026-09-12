"""Mock backend. Same HTTP contract as backend/, in-memory state, scripted agent.

There is no feature flag and no shared code with the real service: you choose one
by choosing a compose file.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
import bcrypt

from app import agent, schemas
from app.documents import empty_document, markdown_to_tiptap
from app.loaders import SUPPORTED_EXTENSIONS, load_pages
from app.seed import seed
from app.store import (
    Collaborator,
    Document,
    FileAsset,
    Message,
    Session,
    Template,
    User,
    new_id,
    now,
    store,
)

JWT_SECRET = os.getenv("JWT_SECRET", "mock_secret")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_MINUTES = 60 * 24 * 7
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "/data/uploads"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "40"))

_bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed()
    yield


app = FastAPI(title="aiper (mock)", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────── auth ────────────────────────────────────


def _token_for(user: User) -> schemas.TokenOut:
    expire = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)
    token = jwt.encode(
        {"sub": str(user.id), "email": user.email, "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    return schemas.TokenOut(access_token=token, user=_user_out(user))


def _user_out(user: User) -> schemas.UserOut:
    return schemas.UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        organisation=user.organisation,
        created_at=user.created_at,
    )


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    unauthorised = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorised
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = store.users.get(uuid.UUID(str(payload["sub"])))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorised from None
    if user is None:
        raise unauthorised
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "service": "mock", "mock": True, "azure_configured": False}


@app.post(
    "/api/v1/auth/register", response_model=schemas.TokenOut, status_code=status.HTTP_201_CREATED
)
async def register(payload: schemas.RegisterRequest) -> schemas.TokenOut:
    if store.user_by_email(payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")
    user = store.add_user(
        User(
            id=new_id(),
            email=payload.email.lower(),
            full_name=payload.full_name.strip(),
            organisation=payload.organisation.strip(),
            hashed_password=bcrypt.hashpw(payload.password.encode()[:72], bcrypt.gensalt()).decode(),
        )
    )
    return _token_for(user)


@app.post("/api/v1/auth/login", response_model=schemas.TokenOut)
async def login(payload: schemas.LoginRequest) -> schemas.TokenOut:
    user = store.user_by_email(payload.email)
    if user is None or not bcrypt.checkpw(payload.password.encode()[:72], user.hashed_password.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return _token_for(user)


@app.get("/api/v1/auth/me", response_model=schemas.UserOut)
async def me(user: CurrentUser) -> schemas.UserOut:
    return _user_out(user)


# ──────────────────────────────── files ───────────────────────────────────


def _file_out(asset: FileAsset) -> schemas.FileOut:
    return schemas.FileOut(
        id=asset.id,
        filename=asset.filename,
        extension=asset.extension,
        size_bytes=asset.size_bytes,
        page_count=asset.page_count,
        indexed=asset.indexed,
        index_error=asset.index_error,
        comparison_role=asset.comparison_role,
        session_id=asset.session_id,
        created_at=asset.created_at,
    )


@app.get("/api/v1/files", response_model=list[schemas.FileOut])
async def list_files(user: CurrentUser) -> list[schemas.FileOut]:
    owned = [f for f in store.files.values() if f.owner_id == user.id]
    return [_file_out(f) for f in sorted(owned, key=lambda f: f.created_at, reverse=True)]


@app.post("/api/v1/files", response_model=schemas.FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    user: CurrentUser,
    file: UploadFile = File(...),
    session_id: uuid.UUID | None = Form(default=None),
    comparison_role: str = Form(default="source"),
) -> schemas.FileOut:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{extension}'. Accepted: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS)),
        )

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {MAX_UPLOAD_MB} MB limit",
        )

    asset = FileAsset(
        id=new_id(),
        owner_id=user.id,
        filename=file.filename or f"upload{extension}",
        extension=extension,
        size_bytes=len(payload),
        comparison_role="target" if comparison_role == "target" else "source",
        session_id=session_id,
    )
    directory = STORAGE_DIR / str(user.id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{asset.id}{extension}"
    destination.write_bytes(payload)
    asset.storage_path = str(destination)
    store.files[asset.id] = asset

    try:
        pages = load_pages(destination, extension)
        if not pages:
            raise ValueError("No extractable text — the file may be a scan without a text layer")
        asset.page_count = store.index_pages(asset, pages)
        asset.indexed = True
    except Exception as exc:  # noqa: BLE001 - reported on the asset, not to the client
        asset.index_error = str(exc)[:500]

    return _file_out(asset)


@app.patch("/api/v1/files/{file_id}/role", response_model=schemas.FileOut)
async def set_role(file_id: uuid.UUID, role: str, user: CurrentUser) -> schemas.FileOut:
    asset = _owned_file(file_id, user)
    asset.comparison_role = "target" if role == "target" else "source"
    return _file_out(asset)


@app.delete("/api/v1/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_file(file_id: uuid.UUID, user: CurrentUser) -> None:
    asset = _owned_file(file_id, user)
    if asset.storage_path:
        Path(asset.storage_path).unlink(missing_ok=True)
    store.drop_file(asset.id)


def _owned_file(file_id: uuid.UUID, user: User) -> FileAsset:
    asset = store.files.get(file_id)
    if asset is None or asset.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return asset


# ────────────────────────────── templates ─────────────────────────────────


def _template_out(template: Template) -> schemas.TemplateOut:
    return schemas.TemplateOut(
        id=template.id,
        key=template.key,
        name=template.name,
        standard=template.standard,
        description=template.description,
        sections=template.sections,
        is_builtin=template.is_builtin,
    )


@app.get("/api/v1/templates", response_model=list[schemas.TemplateOut])
async def list_templates(user: CurrentUser) -> list[schemas.TemplateOut]:
    visible = [
        t for t in store.templates.values() if t.is_builtin or t.owner_id == user.id
    ]
    return [
        _template_out(t) for t in sorted(visible, key=lambda t: (not t.is_builtin, t.name))
    ]


@app.post(
    "/api/v1/templates", response_model=schemas.TemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_template(
    payload: schemas.TemplateCreate, user: CurrentUser
) -> schemas.TemplateOut:
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", payload.name.lower()).strip("_") or "template"
    template = Template(
        id=new_id(),
        key=f"{slug}_{uuid.uuid4().hex[:6]}",
        name=payload.name,
        standard=payload.standard,
        description=payload.description,
        sections=[s.model_dump() for s in payload.sections],
        is_builtin=False,
        owner_id=user.id,
    )
    store.templates[template.id] = template
    return _template_out(template)


@app.delete("/api/v1/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_template(template_id: uuid.UUID, user: CurrentUser) -> None:
    template = store.templates.get(template_id)
    if template is None or template.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    store.templates.pop(template_id)


# ──────────────────────────────── chat ────────────────────────────────────


def _message_out(message: Message) -> schemas.MessageOut:
    return schemas.MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        mode=message.mode,
        activity=message.activity,
        attachments=message.attachments,
        created_at=message.created_at,
    )


def _session_out(session: Session) -> schemas.SessionOut:
    return schemas.SessionOut(
        id=session.id,
        title=session.title,
        mode=session.mode,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@app.get("/api/v1/chat/sessions", response_model=list[schemas.SessionOut])
async def list_sessions(user: CurrentUser) -> list[schemas.SessionOut]:
    owned = [s for s in store.sessions.values() if s.owner_id == user.id]
    return [_session_out(s) for s in sorted(owned, key=lambda s: s.updated_at, reverse=True)]


@app.get("/api/v1/chat/sessions/{session_id}", response_model=schemas.SessionDetail)
async def get_session(session_id: uuid.UUID, user: CurrentUser) -> schemas.SessionDetail:
    session = _owned_session(session_id, user)
    return schemas.SessionDetail(
        **_session_out(session).model_dump(),
        messages=[_message_out(m) for m in session.messages],
    )


@app.delete("/api/v1/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_session(session_id: uuid.UUID, user: CurrentUser) -> None:
    store.sessions.pop(_owned_session(session_id, user).id, None)


def _owned_session(session_id: uuid.UUID, user: User) -> Session:
    session = store.sessions.get(session_id)
    if session is None or session.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return session


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post("/api/v1/chat/stream")
async def stream(payload: schemas.ChatRequest, user: CurrentUser) -> StreamingResponse:
    if payload.session_id:
        session = _owned_session(payload.session_id, user)
        session.mode = payload.mode
    else:
        session = Session(
            id=new_id(),
            owner_id=user.id,
            title=(payload.message.strip().splitlines() or ["New conversation"])[0][:120],
            mode=payload.mode,
        )
        store.sessions[session.id] = session

    async def generate() -> AsyncIterator[str]:
        attachment_ids = [i for i in payload.attachment_ids if i in store.files]
        session.messages.append(
            Message(
                id=new_id(),
                role="user",
                content=payload.message,
                mode=payload.mode,
                attachments=[
                    {"id": str(i), "filename": store.files[i].filename} for i in attachment_ids
                ],
            )
        )
        session.updated_at = now()

        yield _sse({"type": "session", "session_id": str(session.id), "title": session.title})

        answer: list[str] = []
        activity: list[dict[str, Any]] = []
        async for event in agent.run_turn(
            store,
            owner_id=user.id,
            question=payload.message,
            mode=payload.mode,
            attachment_ids=attachment_ids,
            target_id=payload.target_attachment_id,
            template_key=payload.template_key,
        ):
            if event["type"] == "token":
                answer.append(event["value"])
            else:
                activity.append(event)
            yield _sse(event)

        message = Message(
            id=new_id(),
            role="assistant",
            content="".join(answer).strip(),
            mode=payload.mode,
            activity=[e for e in activity if e["type"] != "status"],
        )
        session.messages.append(message)
        session.updated_at = now()

        yield _sse(
            {
                "type": "final",
                "message_id": str(message.id),
                "session_id": str(session.id),
                "content": message.content,
            }
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ────────────────────────────── documents ─────────────────────────────────


def _revision_out(revision: Any) -> schemas.RevisionOut:
    return schemas.RevisionOut(
        id=revision.id,
        revision_number=revision.revision_number,
        parent_revision_id=revision.parent_revision_id,
        commit_message=revision.commit_message,
        source=revision.source,
        author_email=revision.author_email,
        author_name=revision.author_name,
        created_at=revision.created_at,
        additions=revision.additions,
        deletions=revision.deletions,
        modifications=revision.modifications,
    )


def _detail(document: Document, access: str) -> schemas.DocumentDetail:
    owner = store.users.get(document.owner_id)
    return schemas.DocumentDetail(
        id=document.id,
        title=document.title,
        revision_count=document.revision_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
        access=access,
        owner_email=owner.email if owner else "",
        content_json=document.content_json or empty_document(),
        revisions=[
            _revision_out(r)
            for r in sorted(document.revisions, key=lambda r: r.revision_number, reverse=True)
        ],
        collaborators=[
            schemas.CollaboratorOut(
                id=c.id,
                email=c.email,
                role=c.role,
                invite_status=c.invite_status,
                created_at=c.created_at,
            )
            for c in document.collaborators
        ],
    )


def _accessible(document_id: uuid.UUID, user: User, *, write: bool = False) -> Document:
    document = store.documents.get(document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    access = store.access(document, user)
    if access is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if write and access == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only access")
    return document


@app.get("/api/v1/documents", response_model=list[schemas.DocumentOut])
async def list_documents(user: CurrentUser) -> list[schemas.DocumentOut]:
    out: list[schemas.DocumentOut] = []
    for document in store.documents.values():
        access = store.access(document, user)
        if access is None:
            continue
        owner = store.users.get(document.owner_id)
        out.append(
            schemas.DocumentOut(
                id=document.id,
                title=document.title,
                revision_count=document.revision_count,
                created_at=document.created_at,
                updated_at=document.updated_at,
                access="owner" if access == "owner" else "shared",
                owner_email=owner.email if owner else "",
            )
        )
    return sorted(out, key=lambda d: d.updated_at, reverse=True)


@app.post(
    "/api/v1/documents",
    response_model=schemas.DocumentDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    payload: schemas.DocumentCreate, user: CurrentUser
) -> schemas.DocumentDetail:
    document = Document(id=new_id(), owner_id=user.id, title=payload.title)
    store.documents[document.id] = document
    store.commit(
        document,
        content_json=payload.content_json or empty_document(),
        message="Document created",
        author=user,
        source="human",
    )
    return _detail(document, "owner")


@app.post(
    "/api/v1/documents/from-markdown",
    response_model=schemas.DocumentDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_markdown(
    payload: schemas.DocumentFromMarkdown, user: CurrentUser
) -> schemas.DocumentDetail:
    document = Document(id=new_id(), owner_id=user.id, title=payload.title)
    store.documents[document.id] = document
    store.commit(
        document,
        content_json=markdown_to_tiptap(payload.markdown),
        message=payload.commit_message,
        author=user,
        source="agent",
    )
    return _detail(document, "owner")


@app.get("/api/v1/documents/{document_id}", response_model=schemas.DocumentDetail)
async def get_document(document_id: uuid.UUID, user: CurrentUser) -> schemas.DocumentDetail:
    document = _accessible(document_id, user)
    return _detail(document, store.access(document, user) or "viewer")


@app.patch("/api/v1/documents/{document_id}", response_model=schemas.DocumentOut)
async def rename_document(
    document_id: uuid.UUID, payload: schemas.TitleUpdate, user: CurrentUser
) -> schemas.DocumentOut:
    document = _accessible(document_id, user, write=True)
    document.title = payload.title
    document.updated_at = now()
    detail = _detail(document, store.access(document, user) or "editor")
    return schemas.DocumentOut(**{k: getattr(detail, k) for k in schemas.DocumentOut.model_fields})


@app.delete("/api/v1/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_document(document_id: uuid.UUID, user: CurrentUser) -> None:
    document = _accessible(document_id, user)
    if document.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can delete a document")
    store.documents.pop(document_id, None)


@app.post("/api/v1/documents/{document_id}/commits", response_model=schemas.DocumentDetail)
async def commit(
    document_id: uuid.UUID, payload: schemas.CommitCreate, user: CurrentUser
) -> schemas.DocumentDetail:
    document = _accessible(document_id, user, write=True)
    store.commit(
        document,
        content_json=payload.content_json,
        message=payload.commit_message,
        author=user,
        source="human",
    )
    return _detail(document, store.access(document, user) or "editor")


@app.get(
    "/api/v1/documents/{document_id}/revisions/{revision_id}/diff",
    response_model=schemas.DiffOut,
)
async def get_diff(
    document_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser
) -> schemas.DiffOut:
    document = _accessible(document_id, user)
    revision = next((r for r in document.revisions if r.id == revision_id), None)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Revision not found")
    return schemas.DiffOut(
        additions=revision.additions,
        deletions=revision.deletions,
        modifications=revision.modifications,
        blocks=[schemas.DiffBlock(**b) for b in revision.blocks],
    )


@app.post(
    "/api/v1/documents/{document_id}/revisions/{revision_id}/restore",
    response_model=schemas.DocumentDetail,
)
async def restore(
    document_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser
) -> schemas.DocumentDetail:
    document = _accessible(document_id, user, write=True)
    revision = next((r for r in document.revisions if r.id == revision_id), None)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Revision not found")
    store.commit(
        document,
        content_json=revision.content_json,
        message=f"Restore revision {revision.revision_number}",
        author=user,
        source="human",
    )
    return _detail(document, store.access(document, user) or "editor")


@app.post(
    "/api/v1/documents/{document_id}/collaborators",
    response_model=schemas.CollaboratorOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_collaborator(
    document_id: uuid.UUID, payload: schemas.CollaboratorCreate, user: CurrentUser
) -> schemas.CollaboratorOut:
    document = _accessible(document_id, user)
    if document.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can share a document")

    email = payload.email.lower()
    if email == user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You already own this document")
    if any(c.email == email for c in document.collaborators):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already shared with that address")

    invitee = store.user_by_email(email)
    share = Collaborator(
        id=new_id(),
        email=email,
        role=payload.role,
        user_id=invitee.id if invitee else None,
        invite_status="accepted" if invitee else "pending",
    )
    document.collaborators.append(share)
    return schemas.CollaboratorOut(
        id=share.id,
        email=share.email,
        role=share.role,
        invite_status=share.invite_status,
        created_at=share.created_at,
    )


@app.delete(
    "/api/v1/documents/{document_id}/collaborators/{collaborator_id}",
    status_code=status.HTTP_204_NO_CONTENT, response_class=Response,
)
async def remove_collaborator(
    document_id: uuid.UUID, collaborator_id: uuid.UUID, user: CurrentUser
) -> None:
    document = _accessible(document_id, user)
    if document.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can manage sharing")
    share = next((c for c in document.collaborators if c.id == collaborator_id), None)
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collaborator not found")
    document.collaborators.remove(share)
