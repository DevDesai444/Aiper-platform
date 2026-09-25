"""Upload and indexing. One page in, one Qdrant point out.

Indexing runs inline rather than on a queue, so the chat turn that follows an
upload can rely on the pages being searchable. A parse or embed failure is
recorded on the asset and surfaced in the Vault — never raised at the uploader.

The upload treats the file as hostile until proven otherwise: the name is cut
to a sanitised basename before anything reads it, the leading bytes must match
the claimed extension, the stream is refused the moment it passes the size
cap, and the parse runs inside a killable child process (app.rag.sandbox).
Nothing user-supplied ever becomes a filesystem path — assets land as
``{storage_dir}/{owner_id}/{asset_id}{ext}``.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from anyio import Path as AsyncPath
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app import schemas
from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import SlidingWindowRateLimiter
from app.db.models import ChatSession, FileAsset
from app.rag import store
from app.rag.loaders import SUPPORTED_EXTENSIONS, ParseRejected
from app.rag.sandbox import ParseCrashed, parse_in_sandbox
from app.rag.scope import accessible_files_clause
from app.services import audit
from app.services.permissions import require_access
from app.services.uploads import CANONICAL_CONTENT_TYPES, sanitize_filename, save_validated

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

upload_rate_limiter = SlidingWindowRateLimiter(
    max_attempts=settings.upload_rate_limit,
    window_seconds=settings.upload_rate_window_seconds,
)


@router.get("", response_model=list[schemas.FileOut])
async def list_files(user: CurrentUser, db: DbSession) -> list[FileAsset]:
    """Every file the caller may read: their own, plus project-shared ones.

    The same predicate retrieval is scoped by — the Vault must never list
    less (or more) than the agent can cite.
    """
    result = await db.execute(
        select(FileAsset)
        .where(accessible_files_clause(user_id=user.id, org_id=user.org_id))
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
    # Counted before any validation: junk uploads spend the caller's allowance
    # too, so the validator itself cannot be hammered for free.
    if not upload_rate_limiter.hit(str(user.id)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many uploads; wait a moment and try again",
        )

    filename = sanitize_filename(file.filename)
    extension = Path(filename).suffix.lower()
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

    asset_id = uuid.uuid4()
    directory = Path(settings.storage_dir) / str(user.id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{asset_id}{extension}"
    size = await save_validated(
        file,
        destination,
        extension=extension,
        max_bytes=settings.max_upload_mb * 1024 * 1024,
    )

    asset = FileAsset(
        id=asset_id,
        owner_id=user.id,
        org_id=user.org_id,
        project_id=project_id,
        session_id=session_id,
        filename=filename,
        content_type=CANONICAL_CONTENT_TYPES[extension],
        extension=extension,
        size_bytes=size,
        storage_path=str(destination),
        comparison_role="target" if comparison_role == "target" else "source",
    )
    db.add(asset)
    await db.flush()

    try:
        pages = await parse_in_sandbox(destination, extension)
        if not pages:
            raise ParseRejected(
                "No extractable text — the file may be a scan without a text layer"
            )
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
    except ParseRejected as exc:
        # Curated messages only: page budgets, deadlines, corrupt containers.
        asset.indexed = False
        asset.index_error = str(exc)[:500]
    except ParseCrashed as exc:
        logger.error("Parser died on %s (asset %s): %s", filename, asset.id, exc)
        asset.indexed = False
        asset.index_error = "The file could not be parsed; it may be corrupt."
    except Exception as exc:  # noqa: BLE001 - reported on the asset, not to the client
        logger.error(
            "Indexing failed for file %s (asset %s): %s", filename, asset.id, exc, exc_info=True
        )
        asset.indexed = False
        asset.index_error = "Indexing failed; see server logs for details."

    # The audit event rides the same transaction as the asset row: if the
    # commit fails, neither the upload nor its audit trace happened.
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.FILE_UPLOAD,
        subject_type="file",
        subject_id=asset.id,
        payload={"filename": asset.filename, "size_bytes": asset.size_bytes},
    )
    try:
        await db.commit()
    except BaseException:
        # The row is lost; leave no bytes (or points) orphaned behind it.
        await AsyncPath(destination).unlink(missing_ok=True)
        try:
            await store.delete_file(org_id=asset.org_id, owner_id=user.id, file_id=asset_id)
        except Exception:  # noqa: BLE001 - the original failure matters more
            logger.exception("Orphan point cleanup failed for asset %s", asset_id)
        raise
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
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.FILE_DELETE,
        subject_type="file",
        subject_id=asset.id,
        payload={"filename": asset.filename},
    )
    await db.delete(asset)
    await db.commit()


async def _owned(file_id: uuid.UUID, owner_id: uuid.UUID, db: DbSession) -> FileAsset:
    asset = (
        await db.execute(select(FileAsset).where(FileAsset.id == file_id))
    ).scalar_one_or_none()
    if asset is None or asset.owner_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return asset
