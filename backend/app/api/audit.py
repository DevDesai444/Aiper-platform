"""Integrity verification: walk a hash chain, report the first broken link.

Verification returns verdicts, never contents: the audit log itself has no
read path through the API (the app role holds no SELECT on it), so what these
endpoints leak is one bit — "your organisation's history is intact" — plus the
position of the first inconsistency when it is not.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import text

from app import schemas
from app.core.deps import CurrentUser, DbSession
from app.services.permissions import require_access

router = APIRouter(prefix="/audit", tags=["audit"])

_VERIFY_AUDIT = text("SELECT * FROM aiper_audit_verify(CAST(:org_id AS uuid))")
_VERIFY_REVISIONS = text("SELECT * FROM aiper_revision_verify(CAST(:document_id AS uuid))")


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
