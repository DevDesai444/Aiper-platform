"""row-level security: the database itself refuses cross-tenant rows

Binds every tenant table to the resolver from migration 0002 with Postgres
row-level security, so that reaching another organisation's rows is impossible
at the SQL level — with the application checks removed, with a forged query,
with a stolen connection string for the runtime role.

The moving parts:

* **Two roles.** ``aiper_app`` is what the API connects as: LOGIN, no
  superuser, no BYPASSRLS, and only the table privileges the routes actually
  use. ``aiper_definer`` is a NOLOGIN BYPASSRLS role that owns the resolver
  and the helper functions, so a policy can call them without recursing into
  the very policies being evaluated. Roles are cluster-wide, so both are
  created idempotently; running this migration requires a superuser (only a
  superuser may create a BYPASSRLS role, and later data backfills rely on
  bypassing the forced policies).

* **Identity is a per-transaction GUC.** The API verifies the bearer token and
  runs ``SELECT set_config('app.user_id', <sub>, true)`` at the start of every
  transaction (``true`` = transaction-local, the parameterised equivalent of
  ``SET LOCAL``, safe on pooled connections because it dies with the
  transaction). ``aiper_current_user_id()`` reads it back; unset means NULL
  means every policy denies. Session-level ``SET`` is never used: a pooled
  connection would leak one request's identity into the next.

* **FORCE ROW LEVEL SECURITY** on every tenant table, so even the table owner
  obeys the policies — a mis-wired admin connection fails closed rather than
  open. Policies are granted to ``aiper_app`` alone.

* **The unauthenticated seams go through SECURITY DEFINER functions** instead
  of weaker policies: looking a user up by email for the legacy login,
  creating/finding an organisation during registration and lazy provisioning,
  and claiming pending shares at registration. Each is a single narrow query
  owned by ``aiper_definer``.

* **The resolver's ancestry anchor is now project-checked** (review finding on
  #3): a document whose ``folder_id`` pointed into another project could pick
  up that foreign folder's grants, because only the anchor's *ancestors* were
  required to stay in the project. The anchor now is too — and the same hole
  is closed structurally with composite foreign keys ``(folder_id,
  project_id)`` and ``(parent_folder_id, project_id)`` referencing
  ``folders (id, project_id)``, so a cross-project reference cannot even be
  written.

Downgrade removes policies, functions and grants but leaves the roles in
place: they are cluster-wide and other databases may share them.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiper_app"
DEFINER_ROLE = "aiper_definer"

# Every table that carries tenant data, and therefore carries policies.
TENANT_TABLES = (
    "users",
    "organisations",
    "projects",
    "folders",
    "documents",
    "revisions",
    "revision_diffs",
    "file_assets",
    "chat_sessions",
    "chat_messages",
    "access_grants",
    "document_collaborators",
    "document_templates",
)

# What the routes actually execute today. A privilege missing here fails loudly
# ("permission denied for table …"), which is the wanted failure mode for a
# route added without thinking about its policy.
APP_GRANTS: dict[str, str] = {
    "users": "SELECT, INSERT",
    "organisations": "SELECT",
    "projects": "SELECT, INSERT",
    "folders": "SELECT, INSERT",
    "documents": "SELECT, INSERT, UPDATE, DELETE",
    "revisions": "SELECT, INSERT",
    "revision_diffs": "SELECT, INSERT",
    "file_assets": "SELECT, INSERT, UPDATE, DELETE",
    "chat_sessions": "SELECT, INSERT, UPDATE, DELETE",
    "chat_messages": "SELECT, INSERT",
    "access_grants": "SELECT, INSERT, UPDATE, DELETE",
    "document_collaborators": "SELECT, INSERT, DELETE",
    "document_templates": "SELECT, INSERT, DELETE",
}

# Shorthand used throughout the policies.
UID = "aiper_current_user_id()"
UORG = f"aiper_user_org_id({UID})"


def _roles() -> None:
    # Cluster-wide and therefore idempotent: a second database migrating in the
    # same cluster (each test run creates one) finds the roles already there.
    # The password is only meaningful for aiper_app (LOGIN); it is re-applied on
    # every upgrade so rotating AIPER_APP_DB_PASSWORD takes effect.
    password = os.environ.get("AIPER_APP_DB_PASSWORD", "aiper_app_password")
    password = password.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN
                CREATE ROLE {DEFINER_ROLE} NOLOGIN;
            END IF;
        END $$
        """
    )
    op.execute(
        f"ALTER ROLE {APP_ROLE} LOGIN PASSWORD '{password}' "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    )
    op.execute(f"ALTER ROLE {DEFINER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS")


def _grants() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}, {DEFINER_ROLE}")
    for table, privileges in APP_GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO {APP_ROLE}")
    # verify_schema() reads the revision stamp at startup.
    op.execute(f"GRANT SELECT ON alembic_version TO {APP_ROLE}")
    # The definer role reads structure and identity on behalf of the resolver
    # and helpers, and performs the two narrow unauthenticated writes.
    op.execute(
        "GRANT SELECT ON users, organisations, projects, folders, documents, access_grants"
        f" TO {DEFINER_ROLE}"
    )
    op.execute(f"GRANT INSERT ON organisations TO {DEFINER_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE ON document_collaborators TO {DEFINER_ROLE}")


# ─────────────────────────────── helper functions ───────────────────────────

# The request identity, as bound by the API for the current transaction.
# Unset (or blank) reads as NULL, and NULL satisfies no policy: a session that
# never authenticated sees nothing and writes nothing.
_CURRENT_USER = """
CREATE OR REPLACE FUNCTION aiper_current_user_id() RETURNS uuid
LANGUAGE sql STABLE AS
$fn$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $fn$
"""

# A user's organisation, read past the policies (users itself is policed, and
# a policy on users that queried users would recurse).
_USER_ORG = """
CREATE OR REPLACE FUNCTION aiper_user_org_id(p_user uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$fn$ SELECT org_id FROM users WHERE id = p_user $fn$
"""

# The legacy login must find an account before any identity exists. One exact
# email match, dev/demo path only (the endpoint is 404 in production).
_AUTH_BY_EMAIL = """
CREATE OR REPLACE FUNCTION aiper_auth_user_by_email(p_email text) RETURNS SETOF users
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$fn$ SELECT * FROM users WHERE email = p_email $fn$
"""

# Registration and lazy provisioning land accounts in an organisation before
# the account exists, so the upsert cannot be policed by identity. The slug is
# computed in Python (app.services.organisations) — the single slug rule.
_ENSURE_ORG = """
CREATE OR REPLACE FUNCTION aiper_ensure_organisation(p_name text, p_slug text)
RETURNS SETOF organisations
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public AS
$fn$
    INSERT INTO organisations (id, name, slug, created_at, updated_at)
    VALUES (gen_random_uuid(), p_name, p_slug, now(), now())
    ON CONFLICT (slug) DO NOTHING;
    SELECT * FROM organisations WHERE slug = p_slug;
$fn$
"""

# Claiming shares addressed to an email before the account existed: the new
# user cannot see those rows (they sit on documents the user cannot reach), so
# the link runs as definer. It links only — a claim confers no grant, exactly
# as before this migration.
_CLAIM_SHARES = """
CREATE OR REPLACE FUNCTION aiper_claim_pending_shares(p_user uuid, p_email text) RETURNS integer
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public AS
$fn$
    WITH claimed AS (
        UPDATE document_collaborators
           SET user_id = p_user, invite_status = 'accepted'
         WHERE email = p_email AND user_id IS NULL
        RETURNING 1
    )
    SELECT count(*)::int FROM claimed
$fn$
"""

# Bootstrap facts for the first grant on a fresh subject: "the creator grants
# themself owner" cannot require an existing owner grant, and the subject row
# is not yet visible through its own SELECT policy mid-transaction.
_IS_PROJECT_CREATOR = """
CREATE OR REPLACE FUNCTION aiper_is_project_creator(p_project uuid, p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$fn$
    SELECT EXISTS (
        SELECT 1 FROM projects p
         WHERE p.id = p_project AND p.created_by = p_user
           AND p.org_id = aiper_user_org_id(p_user)
    )
$fn$
"""

_IS_DOCUMENT_OWNER = """
CREATE OR REPLACE FUNCTION aiper_is_document_owner(p_document uuid, p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$fn$
    SELECT EXISTS (
        SELECT 1 FROM documents d
         WHERE d.id = p_document AND d.owner_id = p_user
           AND d.org_id = aiper_user_org_id(p_user)
    )
$fn$
"""

# The resolver, re-created with one change: the ancestry anchor is now
# project-checked, exactly like the ancestors always were. Everything else is
# byte-for-byte migration 0002. (Review finding on #3: a document whose
# folder_id pointed into another project anchored the walk at that foreign
# folder, so a grant on it — same organisation, different project — carried
# in. The composite foreign keys below make such a row unwritable; this keeps
# the resolver correct even about data that predates them.)
_RESOLVER = """
CREATE OR REPLACE FUNCTION aiper_effective_access(
    p_user       uuid,
    p_subject    aiper_subject,
    p_subject_id uuid
) RETURNS aiper_role
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    -- Bounds the ancestor walk so a parent_folder_id cycle cannot spin
    -- forever. Real trees are orders of magnitude shallower.
    c_max_depth CONSTANT int := 50;

    v_user_org    uuid;
    v_subject_org uuid;
    v_project     uuid;
    v_folder      uuid;
    v_role        aiper_role;
BEGIN
    IF p_user IS NULL OR p_subject IS NULL OR p_subject_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT u.org_id INTO v_user_org FROM users u WHERE u.id = p_user;
    IF v_user_org IS NULL THEN
        RETURN NULL;                      -- no such user
    END IF;

    -- Locate the subject: its organisation, its project, and the folder it
    -- hangs under (NULL when it sits at the project root).
    IF p_subject = 'project' THEN
        SELECT pr.org_id, pr.id
          INTO v_subject_org, v_project
          FROM projects pr
         WHERE pr.id = p_subject_id;

    ELSIF p_subject = 'folder' THEN
        SELECT pr.org_id, f.project_id, f.id
          INTO v_subject_org, v_project, v_folder
          FROM folders f
          JOIN projects pr ON pr.id = f.project_id
         WHERE f.id = p_subject_id;

    ELSIF p_subject = 'document' THEN
        SELECT pr.org_id, d.project_id, d.folder_id
          INTO v_subject_org, v_project, v_folder
          FROM documents d
          JOIN projects pr ON pr.id = d.project_id
         WHERE d.id = p_subject_id;

    ELSE
        RETURN NULL;                      -- unreachable while the enum holds
    END IF;

    IF v_subject_org IS NULL THEN
        RETURN NULL;                      -- subject does not exist
    END IF;

    -- The organisation boundary is absolute. Checked before any grant is
    -- read, so a grant row inserted across organisations is inert.
    IF v_subject_org <> v_user_org THEN
        RETURN NULL;
    END IF;

    WITH RECURSIVE ancestry AS (
        SELECT f.id, f.parent_folder_id, 1 AS depth
          FROM folders f
          -- The anchor obeys the same rule as the ancestors: a folder from
          -- another project carries nothing, wherever it was referenced from.
         WHERE f.id = v_folder
           AND f.project_id = v_project
        UNION ALL
        SELECT parent.id, parent.parent_folder_id, child.depth + 1
          FROM folders parent
          JOIN ancestry child ON parent.id = child.parent_folder_id
          -- Same-project parentage is a structural invariant; refusing to
          -- leave the project here means a bad parent_folder_id cannot carry
          -- a grant in from somewhere else.
         WHERE parent.project_id = v_project
           AND child.depth < c_max_depth
    )
    SELECT g.role
      INTO v_role
      FROM access_grants g
     WHERE g.user_id = p_user
       AND (
              -- granted directly on the subject
              (g.subject_type = p_subject AND g.subject_id = p_subject_id)
              -- inherited from the containing folder or any of its ancestors
           OR (p_subject <> 'project'
               AND g.subject_type = 'folder'
               AND g.subject_id IN (SELECT a.id FROM ancestry a))
              -- inherited from the project
           OR (p_subject <> 'project'
               AND g.subject_type = 'project'
               AND g.subject_id = v_project)
          )
     ORDER BY CASE g.role
                  WHEN 'owner'  THEN 3
                  WHEN 'editor' THEN 2
                  WHEN 'viewer' THEN 1
              END DESC
     LIMIT 1;

    RETURN v_role;                        -- NULL when no grant applies
END;
$fn$
"""

FUNCTIONS: dict[str, str] = {
    "aiper_current_user_id()": _CURRENT_USER,
    "aiper_user_org_id(uuid)": _USER_ORG,
    "aiper_auth_user_by_email(text)": _AUTH_BY_EMAIL,
    "aiper_ensure_organisation(text, text)": _ENSURE_ORG,
    "aiper_claim_pending_shares(uuid, text)": _CLAIM_SHARES,
    "aiper_is_project_creator(uuid, uuid)": _IS_PROJECT_CREATOR,
    "aiper_is_document_owner(uuid, uuid)": _IS_DOCUMENT_OWNER,
}


def _functions() -> None:
    for signature, body in FUNCTIONS.items():
        op.execute(body)
        name = signature.split("(", 1)[0]
        if name != "aiper_current_user_id":
            op.execute(f"ALTER FUNCTION {signature} OWNER TO {DEFINER_ROLE}")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        # The definer role needs EXECUTE too: SECURITY DEFINER bodies run as it
        # and call the migration-user-owned helpers (aiper_current_user_id).
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}, {DEFINER_ROLE}")

    # The resolver already exists (0002); re-create with the anchor fix and
    # move it to the definer role — under FORCE RLS its internal reads must
    # bypass the policies that call it, or evaluation would recurse.
    op.execute(_RESOLVER)
    op.execute(
        f"ALTER FUNCTION aiper_effective_access(uuid, aiper_subject, uuid) OWNER TO {DEFINER_ROLE}"
    )
    op.execute("REVOKE ALL ON FUNCTION aiper_effective_access(uuid, aiper_subject, uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION aiper_effective_access(uuid, aiper_subject, uuid)"
        f" TO {APP_ROLE}, {DEFINER_ROLE}"
    )


# ─────────────────────────── structural consistency ─────────────────────────


def _composite_foreign_keys() -> None:
    """Make a cross-project folder reference unwritable, not merely inert.

    ``folders (id, project_id)`` becomes a referenceable pair; a document's
    ``(folder_id, project_id)`` and a folder's ``(parent_folder_id,
    project_id)`` must both point at it. MATCH SIMPLE semantics keep NULL
    folder references valid, and the column-targeted ``SET NULL (folder_id)``
    (PostgreSQL 15+) preserves the existing behaviour of a deleted folder
    dropping its documents at the project root — without nulling project_id.
    """
    op.execute("ALTER TABLE folders ADD CONSTRAINT uq_folders_id_project UNIQUE (id, project_id)")

    op.execute("ALTER TABLE documents DROP CONSTRAINT fk_documents_folder")
    op.execute(
        """
        ALTER TABLE documents ADD CONSTRAINT fk_documents_folder_project
        FOREIGN KEY (folder_id, project_id) REFERENCES folders (id, project_id)
        ON DELETE SET NULL (folder_id)
        """
    )

    op.execute("ALTER TABLE folders DROP CONSTRAINT folders_parent_folder_id_fkey")
    op.execute(
        """
        ALTER TABLE folders ADD CONSTRAINT fk_folders_parent_project
        FOREIGN KEY (parent_folder_id, project_id) REFERENCES folders (id, project_id)
        ON DELETE CASCADE
        """
    )


# ──────────────────────────── relocation guards ─────────────────────────────

# WITH CHECK cannot see the OLD row, so "you may edit this document" and "you
# may move it there" cannot be told apart in a policy alone. These triggers
# hold the location-and-identity invariants that need OLD:
#   * org_id and owner_id never change;
#   * relocation requires editor access on the destination;
#   * a folder never changes project (its subtree's project_id would not
#     follow — the composite FK already blocks it for folders with children).
# The guards constrain exactly the sessions row security constrains: a role
# with BYPASSRLS (or a superuser) is administrative and skips them, so admin
# tooling and test fixtures can rearrange trees the way they always could.
_GUARD_EXEMPT = """
CREATE OR REPLACE FUNCTION aiper_rls_exempt() RETURNS boolean
LANGUAGE sql STABLE AS
$fn$
    SELECT coalesce(
        (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user),
        false)
$fn$
"""

_DOCUMENT_GUARD = """
CREATE OR REPLACE FUNCTION aiper_documents_guard() RETURNS trigger
LANGUAGE plpgsql AS
$fn$
BEGIN
    IF aiper_rls_exempt() THEN
        RETURN NEW;
    END IF;
    IF NEW.org_id IS DISTINCT FROM OLD.org_id THEN
        RAISE EXCEPTION 'documents.org_id is immutable';
    END IF;
    IF NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
        RAISE EXCEPTION 'documents.owner_id is immutable';
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.folder_id IS DISTINCT FROM OLD.folder_id THEN
        -- coalesce: a NULL resolver answer (no access at all) must deny, and
        -- NULL NOT IN (...) would otherwise read as "do not raise".
        IF NOT coalesce(
            (CASE WHEN NEW.folder_id IS NULL
                  THEN aiper_effective_access(aiper_current_user_id(), 'project', NEW.project_id)
                  ELSE aiper_effective_access(aiper_current_user_id(), 'folder', NEW.folder_id)
             END) IN ('owner', 'editor'), false) THEN
            RAISE EXCEPTION 'moving a document requires editor access to the destination';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$
"""

_FOLDER_GUARD = """
CREATE OR REPLACE FUNCTION aiper_folders_guard() RETURNS trigger
LANGUAGE plpgsql AS
$fn$
BEGIN
    IF aiper_rls_exempt() THEN
        RETURN NEW;
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id THEN
        RAISE EXCEPTION 'folders.project_id is immutable';
    END IF;
    IF NEW.parent_folder_id IS DISTINCT FROM OLD.parent_folder_id THEN
        IF NOT coalesce(
            (CASE WHEN NEW.parent_folder_id IS NULL
                  THEN aiper_effective_access(aiper_current_user_id(), 'project', NEW.project_id)
                  ELSE aiper_effective_access(aiper_current_user_id(), 'folder', NEW.parent_folder_id)
             END) IN ('owner', 'editor'), false) THEN
            RAISE EXCEPTION 'moving a folder requires editor access to the destination';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$
"""


def _guards() -> None:
    op.execute(_GUARD_EXEMPT)
    op.execute(_DOCUMENT_GUARD)
    op.execute(_FOLDER_GUARD)
    op.execute(
        "CREATE TRIGGER documents_relocation_guard BEFORE UPDATE ON documents"
        " FOR EACH ROW EXECUTE FUNCTION aiper_documents_guard()"
    )
    op.execute(
        "CREATE TRIGGER folders_relocation_guard BEFORE UPDATE ON folders"
        " FOR EACH ROW EXECUTE FUNCTION aiper_folders_guard()"
    )


# ────────────────────────────────── policies ────────────────────────────────


def _policies() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    def policy(table: str, command: str, *, using: str | None = None, check: str | None = None) -> None:
        name = f"{table}_{command.lower().replace(' ', '_')}"
        clauses = ""
        if using is not None:
            clauses += f" USING ({using})"
        if check is not None:
            clauses += f" WITH CHECK ({check})"
        op.execute(f"CREATE POLICY {name} ON {table} FOR {command} TO {APP_ROLE}{clauses}")

    # ── identity ──
    # Own row before provisioning has finished; the rest of the directory only
    # within one's organisation (listings and shares display names/emails).
    policy("users", "SELECT", using=f"id = {UID} OR org_id = {UORG}")
    # Self-provisioning only: the verified token subject may create exactly the
    # row that mirrors it. Registration binds the new id the same way.
    policy("users", "INSERT", check=f"id = {UID}")
    # No UPDATE or DELETE policy: account mutation is not an app-role operation.

    policy("organisations", "SELECT", using=f"id = {UORG}")
    # Writes go through aiper_ensure_organisation() alone.

    # ── the tree ──
    resolver = "aiper_effective_access"
    policy(
        "projects",
        "SELECT",
        using=f"org_id = {UORG} AND {resolver}({UID}, 'project', id) IS NOT NULL",
    )
    policy("projects", "INSERT", check=f"org_id = {UORG} AND created_by = {UID}")
    policy(
        "projects",
        "UPDATE",
        using=f"{resolver}({UID}, 'project', id) IN ('owner', 'editor')",
        check=f"org_id = {UORG}",
    )
    policy("projects", "DELETE", using=f"{resolver}({UID}, 'project', id) = 'owner'")

    destination_folder = (
        f"(CASE WHEN parent_folder_id IS NULL"
        f" THEN {resolver}({UID}, 'project', project_id)"
        f" ELSE {resolver}({UID}, 'folder', parent_folder_id) END) IN ('owner', 'editor')"
    )
    policy("folders", "SELECT", using=f"{resolver}({UID}, 'folder', id) IS NOT NULL")
    policy("folders", "INSERT", check=destination_folder)
    policy(
        "folders",
        "UPDATE",
        using=f"{resolver}({UID}, 'folder', id) IN ('owner', 'editor')",
        check=destination_folder,
    )
    policy("folders", "DELETE", using=f"{resolver}({UID}, 'folder', id) = 'owner'")

    destination_document = (
        f"(CASE WHEN folder_id IS NULL"
        f" THEN {resolver}({UID}, 'project', project_id)"
        f" ELSE {resolver}({UID}, 'folder', folder_id) END) IN ('owner', 'editor')"
    )
    policy(
        "documents",
        "SELECT",
        using=f"org_id = {UORG} AND {resolver}({UID}, 'document', id) IS NOT NULL",
    )
    policy(
        "documents",
        "INSERT",
        check=f"org_id = {UORG} AND owner_id = {UID} AND {destination_document}",
    )
    # WITH CHECK re-derives access from the stored row (the statement snapshot,
    # i.e. the pre-update location); the relocation guard trigger authorises
    # the destination, because only a trigger can see OLD next to NEW.
    policy(
        "documents",
        "UPDATE",
        using=f"org_id = {UORG} AND {resolver}({UID}, 'document', id) IN ('owner', 'editor')",
        check=f"org_id = {UORG} AND {resolver}({UID}, 'document', id) IN ('owner', 'editor')",
    )
    policy(
        "documents",
        "DELETE",
        using=f"org_id = {UORG} AND {resolver}({UID}, 'document', id) = 'owner'",
    )

    # ── history ──
    # Append-only for the app role by omission: no UPDATE or DELETE policy
    # exists, so those commands match nothing. Deleting a document still
    # removes its revisions — referential cascades are internal to Postgres
    # and not subject to row security.
    policy("revisions", "SELECT", using=f"{resolver}({UID}, 'document', document_id) IS NOT NULL")
    policy(
        "revisions",
        "INSERT",
        check=(
            f"{resolver}({UID}, 'document', document_id) IN ('owner', 'editor')"
            f" AND author_id = {UID}"
        ),
    )

    policy(
        "revision_diffs",
        "SELECT",
        using="EXISTS (SELECT 1 FROM revisions r WHERE r.id = revision_id)",
    )
    policy(
        "revision_diffs",
        "INSERT",
        check=(
            "EXISTS (SELECT 1 FROM revisions r WHERE r.id = revision_id"
            f" AND {resolver}({UID}, 'document', r.document_id) IN ('owner', 'editor'))"
        ),
    )

    # ── files ──
    # The same rule the retrieval plane enforces (app.rag.scope
    # accessible_files_clause): org boundary first, then own uploads or a
    # project the resolver admits. Writes stay the uploader's: filing into a
    # project takes editor access on it, and only the owner retitles or
    # removes an asset.
    own = f"owner_id = {UID} AND org_id = {UORG}"
    readable_file = (
        f"org_id = {UORG} AND (owner_id = {UID}"
        f" OR (project_id IS NOT NULL"
        f" AND {resolver}({UID}, 'project', project_id) IS NOT NULL))"
    )
    file_write = (
        f"{own} AND (project_id IS NULL"
        f" OR {resolver}({UID}, 'project', project_id) IN ('owner', 'editor'))"
    )
    policy("file_assets", "SELECT", using=readable_file)
    policy("file_assets", "INSERT", check=file_write)
    policy("file_assets", "UPDATE", using=own, check=file_write)
    policy("file_assets", "DELETE", using=own)

    # ── private-to-owner tables ──
    policy("chat_sessions", "SELECT", using=own)
    policy("chat_sessions", "INSERT", check=own)
    policy("chat_sessions", "UPDATE", using=own, check=own)
    policy("chat_sessions", "DELETE", using=own)

    in_own_session = (
        "EXISTS (SELECT 1 FROM chat_sessions s"
        f" WHERE s.id = session_id AND s.owner_id = {UID})"
    )
    policy("chat_messages", "SELECT", using=in_own_session)
    policy("chat_messages", "INSERT", check=in_own_session)

    # ── sharing ──
    policy(
        "access_grants",
        "SELECT",
        using=(
            f"org_id = {UORG} AND (user_id = {UID}"
            f" OR {resolver}({UID}, subject_type, subject_id) IS NOT NULL)"
        ),
    )
    grant_check = (
        f"org_id = {UORG}"
        f" AND aiper_user_org_id(user_id) = {UORG}"
        f" AND granted_by = {UID}"
        f" AND ({resolver}({UID}, subject_type, subject_id) = 'owner'"
        # The first grant on a fresh subject: its creator grants themself
        # owner before any grant exists to derive ownership from.
        f" OR (user_id = {UID} AND role = 'owner' AND ("
        f"      (subject_type = 'project' AND aiper_is_project_creator(subject_id, {UID}))"
        f"   OR (subject_type = 'document' AND aiper_is_document_owner(subject_id, {UID})))))"
    )
    policy("access_grants", "INSERT", check=grant_check)
    # A re-grant (upsert) is the subject owner moving the role; nobody edits
    # their own grant row.
    policy(
        "access_grants",
        "UPDATE",
        using=f"{resolver}({UID}, subject_type, subject_id) = 'owner'",
        check=grant_check,
    )
    policy(
        "access_grants",
        "DELETE",
        using=(
            f"org_id = {UORG} AND"
            f" ({resolver}({UID}, subject_type, subject_id) = 'owner' OR user_id = {UID})"
        ),
    )

    policy(
        "document_collaborators",
        "SELECT",
        using=f"{resolver}({UID}, 'document', document_id) IS NOT NULL",
    )
    policy(
        "document_collaborators",
        "INSERT",
        check=f"{resolver}({UID}, 'document', document_id) = 'owner'",
    )
    policy(
        "document_collaborators",
        "DELETE",
        using=f"{resolver}({UID}, 'document', document_id) = 'owner'",
    )
    # Claiming (the one UPDATE) runs through aiper_claim_pending_shares().

    # ── templates ──
    policy("document_templates", "SELECT", using=f"is_builtin OR owner_id = {UID}")
    policy("document_templates", "INSERT", check=f"owner_id = {UID} AND NOT is_builtin")
    policy("document_templates", "DELETE", using=f"owner_id = {UID} AND NOT is_builtin")
    # Built-ins are seeded by the admin engine at startup; no app-role path
    # writes them.


def upgrade() -> None:
    _roles()
    _grants()
    _functions()
    _composite_foreign_keys()
    _guards()
    _policies()


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        # Policies belong to the table; dropping them by name keeps the
        # downgrade honest about what 0003 created.
        op.execute(
            f"""
            DO $$
            DECLARE p record;
            BEGIN
                FOR p IN SELECT policyname FROM pg_policies
                          WHERE schemaname = 'public' AND tablename = '{table}' LOOP
                    EXECUTE format('DROP POLICY %I ON {table}', p.policyname);
                END LOOP;
            END $$
            """
        )

    op.execute("DROP TRIGGER IF EXISTS documents_relocation_guard ON documents")
    op.execute("DROP TRIGGER IF EXISTS folders_relocation_guard ON folders")
    op.execute("DROP FUNCTION IF EXISTS aiper_documents_guard()")
    op.execute("DROP FUNCTION IF EXISTS aiper_folders_guard()")
    op.execute("DROP FUNCTION IF EXISTS aiper_rls_exempt()")

    op.execute("ALTER TABLE folders DROP CONSTRAINT fk_folders_parent_project")
    op.execute(
        "ALTER TABLE folders ADD FOREIGN KEY (parent_folder_id)"
        " REFERENCES folders (id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE documents DROP CONSTRAINT fk_documents_folder_project")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT fk_documents_folder FOREIGN KEY (folder_id)"
        " REFERENCES folders (id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE folders DROP CONSTRAINT uq_folders_id_project")

    for signature in list(FUNCTIONS)[::-1]:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    # The resolver keeps the project-checked anchor — downgrading the policies
    # is no reason to reopen a closed hole — but returns to migration-user
    # ownership, since the definer role no longer exists for this database.
    op.execute("ALTER FUNCTION aiper_effective_access(uuid, aiper_subject, uuid) OWNER TO CURRENT_USER")
    op.execute("GRANT EXECUTE ON FUNCTION aiper_effective_access(uuid, aiper_subject, uuid) TO PUBLIC")

    for table, privileges in APP_GRANTS.items():
        op.execute(f"REVOKE {privileges} ON {table} FROM {APP_ROLE}")
    op.execute(f"REVOKE SELECT ON alembic_version FROM {APP_ROLE}")
    op.execute(
        "REVOKE ALL ON users, organisations, projects, folders, documents, access_grants,"
        f" document_collaborators FROM {DEFINER_ROLE}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}, {DEFINER_ROLE}")
    # The roles themselves stay: they are cluster-wide and shared.
