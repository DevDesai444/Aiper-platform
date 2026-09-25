"""tamper-evident history: the hash-chained audit log and revision hashes

Two records that must survive a hostile or buggy writer:

* **audit_log** — one append-only row per security-relevant action. Each row
  carries ``row_hash = sha256(prev_hash ‖ canonical fields)`` where
  ``prev_hash`` is the previous row's hash *within the same organisation*
  (genesis: 64 zeros). Rewriting or removing any row breaks every hash after
  it, so tampering is detectable from the first bad link. A BEFORE INSERT
  trigger computes the chain (client-supplied hashes are overwritten,
  never trusted), an advisory transaction lock per organisation serialises
  concurrent inserts so the chain stays linear, and UPDATE/DELETE raise
  unconditionally — for every role, owner included. The table carries **no
  foreign keys** on purpose: a cascade from users or organisations would
  rewrite or remove evidence, and evidence must outlive its subject.

* **revisions.content_hash / chain_hash** — revisions were already immutable
  by convention; now each one hashes its content and links to its parent's
  chain hash, computed by the same trigger discipline on the write path every
  revision already takes (``_commit`` → INSERT). A revision edited in place no
  longer matches its own hash, and a revision spliced into history no longer
  matches its child's.

Canonical serialisation lives in exactly one place per chain — the SQL hash
functions — and the verifiers recompute with those same functions, so the
trigger and the verifier cannot drift apart. Verification is exposed through
SECURITY DEFINER functions that gate on the caller's identity GUC and return
verdicts, not contents.

The one honest limit: a role that can drop these triggers (the table owner or
a superuser) can rewrite anything. The chain makes that rewriting *evident* —
recomputation fails from the first altered row — and periodic anchoring of
each organisation's head hash outside the database is the (out of scope)
complement.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiper_app"
DEFINER_ROLE = "aiper_definer"

GENESIS = "repeat('0', 64)"

# ────────────────────────────── hash functions ──────────────────────────────

# chr(31) is the ASCII unit separator: it cannot appear in a uuid, a role name
# or an ISO timestamp, so no concatenation of fields can imitate another.
# jsonb::text is deterministic for a given value (keys are stored sorted), so
# the payload needs no further canonicalisation. Declared IMMUTABLE: every
# input is serialised through fixed-format, locale-free conversions.
_AUDIT_ROW_HASH = """
CREATE OR REPLACE FUNCTION aiper_audit_row_hash(
    p_prev         text,
    p_org          uuid,
    p_actor        uuid,
    p_action       text,
    p_subject_type text,
    p_subject_id   uuid,
    p_created_at   timestamptz,
    p_payload      jsonb
) RETURNS text
LANGUAGE sql IMMUTABLE AS
$fn$
    SELECT encode(sha256(convert_to(
        coalesce(p_prev, '')                                            || chr(31) ||
        p_org::text                                                     || chr(31) ||
        coalesce(p_actor::text, '')                                     || chr(31) ||
        p_action                                                        || chr(31) ||
        p_subject_type                                                  || chr(31) ||
        coalesce(p_subject_id::text, '')                                || chr(31) ||
        to_char(p_created_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')                        || chr(31) ||
        p_payload::text,
        'UTF8')), 'hex')
$fn$
"""

_REVISION_CONTENT_HASH = """
CREATE OR REPLACE FUNCTION aiper_revision_content_hash(
    p_content_text text,
    p_content_json jsonb
) RETURNS text
LANGUAGE sql IMMUTABLE AS
$fn$
    SELECT encode(sha256(convert_to(
        coalesce(p_content_text, '') || chr(31) || coalesce(p_content_json::text, ''),
        'UTF8')), 'hex')
$fn$
"""

_REVISION_CHAIN_HASH = """
CREATE OR REPLACE FUNCTION aiper_revision_chain_hash(
    p_parent_chain    text,
    p_document        uuid,
    p_revision_number integer,
    p_parent_revision uuid,
    p_author          uuid,
    p_source          text,
    p_commit_message  text,
    p_created_at      timestamptz,
    p_content_hash    text
) RETURNS text
LANGUAGE sql IMMUTABLE AS
$fn$
    SELECT encode(sha256(convert_to(
        coalesce(p_parent_chain, '')                                    || chr(31) ||
        p_document::text                                                || chr(31) ||
        p_revision_number::text                                         || chr(31) ||
        coalesce(p_parent_revision::text, '')                           || chr(31) ||
        coalesce(p_author::text, '')                                    || chr(31) ||
        p_source                                                        || chr(31) ||
        p_commit_message                                                || chr(31) ||
        to_char(p_created_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')                        || chr(31) ||
        p_content_hash,
        'UTF8')), 'hex')
$fn$
"""

# ─────────────────────────────────── triggers ────────────────────────────────

# SECURITY DEFINER: under FORCE ROW LEVEL SECURITY the inserting app role has
# no SELECT on audit_log at all, and the previous head must still be readable
# to extend the chain. The advisory lock serialises writers per organisation;
# it is transaction-scoped, so the lock is held until the row is committed and
# the next writer reads a committed head.
_AUDIT_BEFORE_INSERT = f"""
CREATE OR REPLACE FUNCTION aiper_audit_before_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
$fn$
DECLARE
    v_prev text;
    v_seq  bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('aiper_audit:' || NEW.org_id::text, 0));
    SELECT a.row_hash, a.seq INTO v_prev, v_seq
      FROM audit_log a
     WHERE a.org_id = NEW.org_id
     ORDER BY a.seq DESC
     LIMIT 1;

    NEW.seq        := coalesce(v_seq, 0) + 1;
    NEW.prev_hash  := coalesce(v_prev, {GENESIS});
    NEW.created_at := coalesce(NEW.created_at, now());
    NEW.row_hash   := aiper_audit_row_hash(
        NEW.prev_hash, NEW.org_id, NEW.actor_id, NEW.action,
        NEW.subject_type, NEW.subject_id, NEW.created_at, NEW.payload);
    RETURN NEW;
END;
$fn$
"""

_BLOCK_MUTATION = """
CREATE OR REPLACE FUNCTION aiper_block_mutation() RETURNS trigger
LANGUAGE plpgsql AS
$fn$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$fn$
"""

_REVISIONS_BEFORE_INSERT = f"""
CREATE OR REPLACE FUNCTION aiper_revisions_before_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
$fn$
DECLARE
    v_parent_chain text;
BEGIN
    NEW.content_hash := aiper_revision_content_hash(NEW.content_text, NEW.content_json);

    IF NEW.parent_revision_id IS NULL THEN
        v_parent_chain := {GENESIS};
    ELSE
        SELECT r.chain_hash INTO v_parent_chain
          FROM revisions r
         WHERE r.id = NEW.parent_revision_id
           AND r.document_id = NEW.document_id;
        IF v_parent_chain IS NULL THEN
            RAISE EXCEPTION 'parent revision % does not belong to document %',
                NEW.parent_revision_id, NEW.document_id;
        END IF;
    END IF;

    NEW.chain_hash := aiper_revision_chain_hash(
        v_parent_chain, NEW.document_id, NEW.revision_number, NEW.parent_revision_id,
        NEW.author_id, NEW.source, NEW.commit_message, NEW.created_at, NEW.content_hash);
    RETURN NEW;
END;
$fn$
"""

# ─────────────────────────────────── verifiers ───────────────────────────────

# Both gate on the caller's identity GUC *inside* the function — a caller with
# no standing gets an empty result, indistinguishable from asking about
# nothing — and return verdicts and positions only, never contents.
_AUDIT_VERIFY = f"""
CREATE OR REPLACE FUNCTION aiper_audit_verify(p_org uuid)
RETURNS TABLE (ok boolean, checked bigint, first_broken_id bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS
$fn$
DECLARE
    r        record;
    v_prev   text   := {GENESIS};
    v_seq    bigint := 0;
    v_count  bigint := 0;
BEGIN
    IF aiper_user_org_id(aiper_current_user_id()) IS DISTINCT FROM p_org THEN
        RETURN;                            -- no standing, no answer
    END IF;

    FOR r IN
        SELECT a.* FROM audit_log a WHERE a.org_id = p_org ORDER BY a.seq
    LOOP
        IF r.seq <> v_seq + 1
           OR r.prev_hash <> v_prev
           OR r.row_hash <> aiper_audit_row_hash(
                  r.prev_hash, r.org_id, r.actor_id, r.action,
                  r.subject_type, r.subject_id, r.created_at, r.payload) THEN
            RETURN QUERY SELECT false, v_count, r.id;
            RETURN;
        END IF;
        v_prev  := r.row_hash;
        v_seq   := r.seq;
        v_count := v_count + 1;
    END LOOP;

    RETURN QUERY SELECT true, v_count, NULL::bigint;
END;
$fn$
"""

_REVISION_VERIFY = f"""
CREATE OR REPLACE FUNCTION aiper_revision_verify(p_document uuid)
RETURNS TABLE (ok boolean, checked integer, first_broken_revision uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS
$fn$
DECLARE
    r        record;
    v_parent text;
    v_count  integer := 0;
BEGIN
    IF aiper_effective_access(aiper_current_user_id(), 'document', p_document) IS NULL THEN
        RETURN;                            -- unreachable documents stay unreachable
    END IF;

    FOR r IN
        SELECT rev.* FROM revisions rev
         WHERE rev.document_id = p_document
         ORDER BY rev.revision_number
    LOOP
        IF r.parent_revision_id IS NULL THEN
            v_parent := {GENESIS};
        ELSE
            SELECT p.chain_hash INTO v_parent
              FROM revisions p
             WHERE p.id = r.parent_revision_id AND p.document_id = p_document;
            v_parent := coalesce(v_parent, {GENESIS});
        END IF;

        IF r.content_hash <> aiper_revision_content_hash(r.content_text, r.content_json)
           OR r.chain_hash <> aiper_revision_chain_hash(
                  v_parent, r.document_id, r.revision_number, r.parent_revision_id,
                  r.author_id, r.source, r.commit_message, r.created_at, r.content_hash) THEN
            RETURN QUERY SELECT false, v_count, r.id;
            RETURN;
        END IF;
        v_count := v_count + 1;
    END LOOP;

    RETURN QUERY SELECT true, v_count, NULL::uuid;
END;
$fn$
"""

FUNCTIONS: dict[str, str] = {
    "aiper_audit_row_hash(text, uuid, uuid, text, text, uuid, timestamptz, jsonb)": _AUDIT_ROW_HASH,
    "aiper_revision_content_hash(text, jsonb)": _REVISION_CONTENT_HASH,
    "aiper_revision_chain_hash(text, uuid, integer, uuid, uuid, text, text, timestamptz, text)": (
        _REVISION_CHAIN_HASH
    ),
    "aiper_audit_before_insert()": _AUDIT_BEFORE_INSERT,
    "aiper_block_mutation()": _BLOCK_MUTATION,
    "aiper_revisions_before_insert()": _REVISIONS_BEFORE_INSERT,
    "aiper_audit_verify(uuid)": _AUDIT_VERIFY,
    "aiper_revision_verify(uuid)": _REVISION_VERIFY,
}


def upgrade() -> None:
    # ── the audit log ──
    op.execute(
        """
        CREATE TABLE audit_log (
            id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            org_id       uuid        NOT NULL,
            -- The chain position, assigned by the trigger under the per-org
            -- advisory lock. The identity id cannot play this role: nextval
            -- fires before the lock is taken, so under concurrency id order
            -- and chain order can disagree.
            seq          bigint      NOT NULL,
            actor_id     uuid,
            action       text        NOT NULL,
            subject_type text        NOT NULL,
            subject_id   uuid,
            payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,
            created_at   timestamptz NOT NULL DEFAULT now(),
            prev_hash    text        NOT NULL,
            row_hash     text        NOT NULL,
            CONSTRAINT uq_audit_log_org_seq UNIQUE (org_id, seq)
        )
        """
    )

    # ── revision hashes ──
    op.execute("ALTER TABLE revisions ADD COLUMN content_hash text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE revisions ADD COLUMN chain_hash text NOT NULL DEFAULT ''")

    for signature, body in FUNCTIONS.items():
        op.execute(body)
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {DEFINER_ROLE}")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        # aiper_app calls the verifiers; the definer role's own SECURITY
        # DEFINER bodies call the identity helpers from migration 0003.
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}, {DEFINER_ROLE}")

    # Chain the rows that predate the hashes, parents strictly before children
    # (a parent always has the lower revision_number in its document).
    op.execute(
        """
        DO $$
        DECLARE
            r        record;
            v_parent text;
            v_content text;
        BEGIN
            FOR r IN
                SELECT id, document_id, parent_revision_id, revision_number,
                       author_id, source, commit_message, created_at,
                       content_text, content_json
                  FROM revisions
                 ORDER BY document_id, revision_number
            LOOP
                IF r.parent_revision_id IS NULL THEN
                    v_parent := repeat('0', 64);
                ELSE
                    SELECT chain_hash INTO v_parent FROM revisions WHERE id = r.parent_revision_id;
                    v_parent := coalesce(v_parent, repeat('0', 64));
                END IF;
                v_content := aiper_revision_content_hash(r.content_text, r.content_json);
                UPDATE revisions
                   SET content_hash = v_content,
                       chain_hash   = aiper_revision_chain_hash(
                           v_parent, r.document_id, r.revision_number, r.parent_revision_id,
                           r.author_id, r.source, r.commit_message, r.created_at, v_content)
                 WHERE id = r.id;
            END LOOP;
        END $$
        """
    )

    # Triggers last: the backfill above is the one legitimate UPDATE the
    # revisions table will ever see.
    op.execute(
        "CREATE TRIGGER audit_log_chain BEFORE INSERT ON audit_log"
        " FOR EACH ROW EXECUTE FUNCTION aiper_audit_before_insert()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log"
        " FOR EACH ROW EXECUTE FUNCTION aiper_block_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log"
        " FOR EACH ROW EXECUTE FUNCTION aiper_block_mutation()"
    )
    op.execute(
        "CREATE TRIGGER revisions_chain BEFORE INSERT ON revisions"
        " FOR EACH ROW EXECUTE FUNCTION aiper_revisions_before_insert()"
    )
    # No delete blocker on revisions: deleting a *document* legitimately
    # cascades its history away; what must never happen is a revision changing
    # after the fact. Same for the materialised diffs.
    op.execute(
        "CREATE TRIGGER revisions_no_update BEFORE UPDATE ON revisions"
        " FOR EACH ROW EXECUTE FUNCTION aiper_block_mutation()"
    )
    op.execute(
        "CREATE TRIGGER revision_diffs_no_update BEFORE UPDATE ON revision_diffs"
        " FOR EACH ROW EXECUTE FUNCTION aiper_block_mutation()"
    )

    # ── row security and privileges ──
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")
    # Append-only and write-only for the app role: rows go in about the
    # caller's own organisation in the caller's own name, and reading comes
    # back only as a verification verdict. TRUNCATE is a privilege the role
    # simply never receives.
    op.execute(
        f"""
        CREATE POLICY audit_log_insert ON audit_log FOR INSERT TO {APP_ROLE}
        WITH CHECK (
            org_id = aiper_user_org_id(aiper_current_user_id())
            AND actor_id = aiper_current_user_id()
        )
        """
    )
    op.execute(f"GRANT INSERT ON audit_log TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON audit_log TO {DEFINER_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE audit_log_id_seq TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON revisions TO {DEFINER_ROLE}")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS revision_diffs_no_update ON revision_diffs")
    op.execute("DROP TRIGGER IF EXISTS revisions_no_update ON revisions")
    op.execute("DROP TRIGGER IF EXISTS revisions_chain ON revisions")
    op.execute("DROP TABLE audit_log")
    for signature in list(FUNCTIONS)[::-1]:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("ALTER TABLE revisions DROP COLUMN chain_hash")
    op.execute("ALTER TABLE revisions DROP COLUMN content_hash")
    op.execute(f"REVOKE SELECT ON revisions FROM {DEFINER_ROLE}")
