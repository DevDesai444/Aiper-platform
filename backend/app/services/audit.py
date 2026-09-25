"""The audit trail: one append-only, hash-chained row per action that matters.

``record_audit`` inserts into ``audit_log`` (migration 0004) inside the
caller's open transaction, so the audit row commits or rolls back atomically
with the action it describes — an action cannot land without its evidence, and
evidence cannot describe an action that never happened.

Everything tamper-evident about the row happens in the database, not here: a
BEFORE INSERT trigger computes ``prev_hash``/``row_hash`` (whatever the client
supplies is overwritten), UPDATE and DELETE raise for every role, and the
insert policy only admits rows about the caller's own organisation in the
caller's own name. This module is deliberately just the vocabulary and the
INSERT.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The event vocabulary, in one place so the log stays greppable.
AUTH_REGISTER = "auth.register"
AUTH_LOGIN = "auth.login"
AUTH_PROVISION = "auth.provision"
PROJECT_CREATE = "project.create"
FOLDER_CREATE = "folder.create"
DOCUMENT_CREATE = "document.create"
DOCUMENT_DELETE = "document.delete"
DOCUMENT_COMMIT = "document.commit"
DOCUMENT_RESTORE = "document.restore"
SHARE_GRANT = "share.grant"
SHARE_REVOKE = "share.revoke"
FILE_UPLOAD = "file.upload"
FILE_DELETE = "file.delete"
TEMPLATE_CREATE = "template.create"
TEMPLATE_DELETE = "template.delete"

_INSERT = text(
    """
    INSERT INTO audit_log (org_id, actor_id, action, subject_type, subject_id, payload)
    VALUES (
        CAST(:org_id AS uuid),
        CAST(:actor_id AS uuid),
        :action,
        :subject_type,
        CAST(:subject_id AS uuid),
        CAST(:payload AS jsonb)
    )
    """
)


async def record_audit(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    subject_type: str,
    subject_id: uuid.UUID | None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one event to the caller's organisation chain, uncommitted.

    The caller's transaction still owns the commit: audit rows ride along with
    the action they describe.
    """
    import json

    await db.execute(
        _INSERT,
        {
            "org_id": str(org_id),
            "actor_id": str(actor_id),
            "action": action,
            "subject_type": subject_type,
            "subject_id": str(subject_id) if subject_id else None,
            "payload": json.dumps(payload or {}, default=str),
        },
    )
