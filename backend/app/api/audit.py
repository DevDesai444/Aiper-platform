"""Integrity verification, and reading the log itself.

Verification returns verdicts, never contents. Reading ("Activity") does
return contents, but only through the same narrow seam: the app role still
holds no SELECT on audit_log directly (0004), so both the verifiers and
``aiper_audit_read`` are SECURITY DEFINER functions that gate on the caller's
own standing and hand back nothing at all — not a 403, not an empty-but-
distinguishable list — when that standing is absent.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app import schemas
from app.core.deps import CurrentUser, DbSession
from app.services.permissions import require_access
from app.services.refs import resolve_ref

router = APIRouter(prefix="/audit", tags=["audit"])

_VERIFY_AUDIT = text("SELECT * FROM aiper_audit_verify(CAST(:org_id AS uuid))")
_VERIFY_REVISIONS = text("SELECT * FROM aiper_revision_verify(CAST(:document_id AS uuid))")
_READ_AUDIT = text(
    "SELECT * FROM aiper_audit_read("
    "CAST(:subject_type AS aiper_subject), CAST(:subject_id AS uuid))"
)


@router.get("/verify", response_model=schemas.AuditVerifyOut)
async def verify_audit_chain(user: CurrentUser, db: DbSession) -> schemas.AuditVerifyOut:
    """Recompute the caller's organisation chain from genesis.

    The SQL function additionally gates on the session identity, so it answers
    for the caller's own organisation or not at all.
    """
    row = (
        (await db.execute(_VERIFY_AUDIT, {"org_id": str(user.org_id)})).mappings().one()
    )
    return schemas.AuditVerifyOut(**row)


@router.get("/documents/{document_id}/verify", response_model=schemas.RevisionVerifyOut)
async def verify_revision_chain(
    document_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> schemas.RevisionVerifyOut:
    """Recompute one document's revision chain, for anyone who can read it."""
    await require_access(db, user, "document", document_id, "viewer")
    row = (
        (await db.execute(_VERIFY_REVISIONS, {"document_id": str(document_id)}))
        .mappings()
        .one()
    )
    return schemas.RevisionVerifyOut(**row)


@router.get("/documents/{document_ref}", response_model=list[schemas.AuditEntryOut])
async def document_audit_log(
    document_ref: str, user: CurrentUser, db: DbSession
) -> list[schemas.AuditEntryOut]:
    """A document's activity log, newest first — for anyone who can view it."""
    document_id = await resolve_ref(db, "document", user.org_id, document_ref)
    if document_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await require_access(db, user, "document", document_id, "viewer")
    rows = (
        (await db.execute(_READ_AUDIT, {"subject_type": "document", "subject_id": str(document_id)}))
        .mappings()
        .all()
    )
    return [schemas.AuditEntryOut(**row) for row in rows]


@router.get("/projects/{project_ref}", response_model=list[schemas.AuditEntryOut])
async def project_audit_log(
    project_ref: str, user: CurrentUser, db: DbSession
) -> list[schemas.AuditEntryOut]:
    """A project's activity log, newest first — for anyone who can view it.

    Direct project events only (create/rename/delete) — events on the
    project's own folders and documents are a separate subject_id each and
    are not rolled up here. Noted as a follow-up, not built: it complicates
    the definer query (a set of subject_ids to gate and union instead of one)
    for a "nice to have" the brief didn't ask this unit to ship.
    """
    project_id = await resolve_ref(db, "project", user.org_id, project_ref)
    if project_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await require_access(db, user, "project", project_id, "viewer")
    rows = (
        (await db.execute(_READ_AUDIT, {"subject_type": "project", "subject_id": str(project_id)}))
        .mappings()
        .all()
    )
    return [schemas.AuditEntryOut(**row) for row in rows]
