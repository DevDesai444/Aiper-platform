"""a read path for the audit log, gated the same way the verifiers are

The app role holds INSERT-only on ``audit_log`` (0004, by design: an
unprivileged writer must not be able to read the very evidence it produces).
Surfacing "Activity" for a project or document needs a read path that still
respects that boundary, so this follows the exact shape of
``aiper_audit_verify``/``aiper_revision_verify``: a ``STABLE SECURITY
DEFINER`` function, owned by ``aiper_definer``, that checks the caller's own
standing via ``aiper_effective_access`` and returns nothing at all when it is
absent — an unreachable subject's log is indistinguishable from an empty one,
the same anti-enumeration posture ``require_access`` already gives every
other route.

Deliberately no ``p_user`` parameter, even though it would read naturally as
the function's first argument: every reader in 0004 takes its identity from
``aiper_current_user_id()`` (the transaction-local GUC the API sets from the
verified bearer token), never from a caller-supplied value, so a bug one
layer up can never trick this function into answering for someone else. This
is a deliberate deviation from the sketch in the kickoff
(``aiper_audit_read(p_user, ...)``) in favour of matching the codebase's own
established convention for this exact class of function.

Joins ``users`` for a display name/email rather than handing back a bare
``actor_id`` — ``aiper_definer`` already holds ``SELECT`` on ``users`` from
0003 for the same reason the resolver needs it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiper_app"
DEFINER_ROLE = "aiper_definer"

_SIGNATURE = "aiper_audit_read(aiper_subject, uuid)"

_AUDIT_READ = """
CREATE OR REPLACE FUNCTION aiper_audit_read(p_subject_type aiper_subject, p_subject_id uuid)
RETURNS TABLE (
    id         bigint,
    action     text,
    actor_id   uuid,
    actor_name text,
    actor_email text,
    created_at timestamptz,
    payload    jsonb
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS
$fn$
BEGIN
    IF aiper_effective_access(aiper_current_user_id(), p_subject_type, p_subject_id) IS NULL THEN
        RETURN;                            -- unreachable subjects stay unreachable
    END IF;

    RETURN QUERY
        -- users.full_name/email are varchar(n); RETURN QUERY requires an exact
        -- type match against the RETURNS TABLE list above, so both are cast to
        -- text rather than declaring the function's output in terms of
        -- another table's column widths.
        SELECT a.id, a.action, a.actor_id, u.full_name::text, u.email::text, a.created_at, a.payload
          FROM audit_log a
          LEFT JOIN users u ON u.id = a.actor_id
         WHERE a.subject_type = p_subject_type::text
           AND a.subject_id = p_subject_id
         ORDER BY a.seq DESC;
END;
$fn$
"""


def upgrade() -> None:
    op.execute(_AUDIT_READ)
    op.execute(f"ALTER FUNCTION {_SIGNATURE} OWNER TO {DEFINER_ROLE}")
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO {APP_ROLE}, {DEFINER_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_SIGNATURE}")
