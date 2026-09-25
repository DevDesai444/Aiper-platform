"""Upload and indexing. One page in, one Qdrant point out.

Indexing runs inline rather than on a queue, so the chat turn that follows an
upload can rely on the pages being searchable. A parse or embed failure is
recorded on the asset and surfaced in the Vault — never raised at the uploader.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from anyio import Path as AsyncPath
from anyio.to_thread import run_sync
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app import schemas
from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.db.models import ChatSession, FileAsset
from app.rag import store
from app.rag.loaders import SUPPORTED_EXTENSIONS, load_pages
from app.services.permissions import require_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


@router.get("", response_model=list[schemas.FileOut])
async def list_files(user: CurrentUser, db: DbSession) -> list[FileAsset]:
    result = await db.execute(
        select(FileAsset)
        .where(FileAsset.owner_id == user.id)
        .order_by(FileAsset.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=schemas.FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    session_id: uuid.UUID | None = Form(default=None),
    project_id: uuid.UUID | None = Form(default=None),
    comparison_role: str = Form(default="source"),
) -> FileAsset:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{extension}'. Accepted: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS)),
        )

    # Both ids come from the client. A conversation must be the caller's own,
    # and filing into a project is a write to it, so it takes editor access —
    # the resolver decides, exactly as it does for the project routes.
    if session_id is not None:
        session = (
            await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        ).scalar_one_or_none()
        if session is None or session.owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    if project_id is not None:
        await require_access(db, user, "project", project_id, "editor")

    payload = await file.read()
    if len(payload) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {settings.max_upload_mb} MB limit",
        )

    asset = FileAsset(
        owner_id=user.id,
        org_id=user.org_id,
        project_id=project_id,
        session_id=session_id,
        filename=file.filename or f"upload{extension}",
        content_type=file.content_type or "",
        extension=extension,
        size_bytes=len(payload),
        comparison_role="target" if comparison_role == "target" else "source",
    )
    db.add(asset)
    await db.flush()

    directory = Path(settings.storage_dir) / str(user.id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{asset.id}{extension}"
    await AsyncPath(destination).write_bytes(payload)
    asset.storage_path = str(destination)

    try:
        pages = await run_sync(load_pages, destination, extension)
        if not pages:
            raise ValueError("No extractable text — the file may be a scan without a text layer")
        # Tenancy on every point comes from the asset row, the single source
        # of truth the retrieval filter is checked against.
        asset.page_count = await store.index_pages(
            owner_id=asset.owner_id,
            org_id=asset.org_id,
            project_id=asset.project_id,
            file_id=asset.id,
            filename=asset.filename,
            session_id=session_id,
            comparison_role=asset.comparison_role,
            pages=pages,
        )
        asset.indexed = True
    except Exception as exc:  # noqa: BLE001 - reported on the asset, not to the client
        logger.error("Indexing failed for file %s (asset %s): %s", file.filename, asset.id, exc, exc_info=True)
        asset.indexed = False
        asset.index_error = "Indexing failed; see server logs for details."

    await db.commit()
    await db.refresh(asset)
    return asset


@router.patch("/{file_id}/role", response_model=schemas.FileOut)
async def set_role(
    file_id: uuid.UUID, role: str, user: CurrentUser, db: DbSession
) -> FileAsset:
    asset = await _owned(file_id, user.id, db)
    asset.comparison_role = "target" if role == "target" else "source"
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_file(file_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    asset = await _owned(file_id, user.id, db)
    await store.delete_file(org_id=asset.org_id, owner_id=user.id, file_id=asset.id)
    if asset.storage_path:
        await AsyncPath(asset.storage_path).unlink(missing_ok=True)
    await db.delete(asset)
    await db.commit()


async def _owned(file_id: uuid.UUID, owner_id: uuid.UUID, db: DbSession) -> FileAsset:
    asset = (
        await db.execute(select(FileAsset).where(FileAsset.id == file_id))
    ).scalar_one_or_none()
    if asset is None or asset.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return asset
