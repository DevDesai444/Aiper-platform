"""tenancy: organisations, projects, folders, access grants and the resolver

Introduces the hierarchy organisation -> project -> folder (nested) -> document.
Access is granted per subject and cascades downward; the organisation boundary
is absolute and no grant can cross it.

Every structural change is paired with a backfill so an existing development
database survives the upgrade: users are grouped into organisations by their
free-text `organisation` string, each document owner gets a "Workspace" project
holding their documents, and owners keep owner-level access through explicit
grants.

The single source of truth for "what may this user do to this subject" is the
SQL function aiper_effective_access, created at the end of this migration.
Python never re-implements the cascade; it calls the function.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Slugify a free-text organisation name: lowercase, runs of non-alphanumerics
# collapsed to a single dash, dashes trimmed from both ends. Names that reduce
# to nothing ("", "!!!") fall back to the catch-all "default" organisation.
_SLUG = """
        COALESCE(
            NULLIF(
                regexp_replace(
                    regexp_replace(lower(btrim({col})), '[^a-z0-9]+', '-', 'g'),
                    '(^-+|-+$)', '', 'g'
                ),
            ''),
        'default')
"""

# Users grouped by the slug rather than by the raw string: "Acme Inc" and
# "Acme, Inc." are the same organisation, and grouping by slug is also what
# keeps organisations.slug unique.
_LABELLED_USERS = f"""
    SELECT
        u.id AS user_id,
        CASE WHEN btrim(u.organisation) = '' THEN 'Default' ELSE btrim(u.organisation) END
            AS org_name,
        {_SLUG.format(col="u.organisation")} AS org_slug
    FROM users u
"""

_ROLE_ENUM = postgresql.ENUM(
    "owner", "editor", "viewer", name="aiper_role", create_type=False
)
_SUBJECT_ENUM = postgresql.ENUM(
    "project", "folder", "document", name="aiper_subject", create_type=False
)


# ─────────────────────────────── the resolver ──────────────────────────────

# Effective access for one (user, subject) pair.
#
#   * The subject's organisation is derived from its project, the one
#     structural truth; the denormalised org_id columns are for RLS and
#     reporting and are deliberately not trusted here.
#   * A user whose organisation differs from the subject's gets NULL
#     unconditionally, before any grant is considered.
#   * Candidate grants are the direct grant on the subject plus, walking
#     upward, its folder, that folder's ancestors and its project.
#   * The effective role is the highest candidate: owner > editor > viewer.
#
# SECURITY DEFINER so that E3's row-level-security policies can call it without
# recursing into the policies they are evaluating; STABLE so it may be used in
# WHERE clauses and index scans within a statement.
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
         WHERE f.id = v_folder
        UNION ALL
        SELECT parent.id, parent.parent_folder_id, child.depth + 1
          FROM folders parent
          JOIN ancestry child ON parent.id = child.parent_folder_id
          -- Same-project parentage is a service-layer invariant; refusing to
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
$fn$;
"""


def upgrade() -> None:
    _create_enums()
    _create_organisations()
    _attach_users_to_organisations()
    _create_projects_and_folders()
    _attach_documents()
    _attach_file_assets_and_sessions()
    _create_access_grants()
    _backfill_grants()
    op.execute(_RESOLVER)


# ──────────────────────────────── structure ───────────────────────────────


def _create_enums() -> None:
    # Created explicitly rather than as a side effect of create_table, so that
    # both tables and the function can reference them in any order.
    op.execute("CREATE TYPE aiper_subject AS ENUM ('project', 'folder', 'document')")
    op.execute("CREATE TYPE aiper_role AS ENUM ('owner', 'editor', 'viewer')")


def _create_organisations() -> None:
    op.create_table(
        "organisations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )


def _attach_users_to_organisations() -> None:
    op.add_column("users", sa.Column("org_id", sa.UUID(), nullable=True))

    op.execute(
        f"""
        WITH labelled AS ({_LABELLED_USERS})
        INSERT INTO organisations (id, name, slug, created_at, updated_at)
        SELECT gen_random_uuid(), min(org_name), org_slug, now(), now()
          FROM labelled
         GROUP BY org_slug
        """
    )
    op.execute(
        f"""
        WITH labelled AS ({_LABELLED_USERS})
        UPDATE users u
           SET org_id = o.id
          FROM labelled l
          JOIN organisations o ON o.slug = l.org_slug
         WHERE u.id = l.user_id
        """
    )

    op.alter_column("users", "org_id", nullable=False)
    # Deleting an organisation is blocked while it still holds accounts:
    # tenant offboarding is a deliberate operation, not a cascade.
    op.create_foreign_key("fk_users_org", "users", "organisations", ["org_id"], ["id"])
    op.create_index("ix_users_org_id", "users", ["org_id"])
    # users.organisation, the old free-text column, is deliberately kept for
    # now: it is still what registration writes and what /auth/me returns.


def _create_projects_and_folders() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_org_id", "projects", ["org_id"])

    op.create_table(
        "folders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("parent_folder_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        # Self-referential: deleting a folder deletes the subtree beneath it.
        # That a parent lives in the same project as its child is enforced in
        # the service layer (see app.services.tree), because a composite FK
        # would need project_id duplicated into the parent reference.
        sa.ForeignKeyConstraint(["parent_folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_folders_project_id", "folders", ["project_id"])
    op.create_index("ix_folders_parent_folder_id", "folders", ["parent_folder_id"])


def _attach_documents() -> None:
    op.add_column("documents", sa.Column("org_id", sa.UUID(), nullable=True))
    op.add_column("documents", sa.Column("project_id", sa.UUID(), nullable=True))
    op.add_column("documents", sa.Column("folder_id", sa.UUID(), nullable=True))

    # One "Workspace" project per document *owner*, not per organisation.
    # Before tenancy a document was reachable only by its owner and the people
    # it was explicitly shared with; pooling every owner's documents into one
    # organisation-wide project would hand each future project grantee their
    # colleagues' documents, which is a privacy regression rather than a
    # migration.
    op.execute(
        """
        CREATE TEMPORARY TABLE _e2_workspace (
            owner_id   uuid PRIMARY KEY,
            project_id uuid NOT NULL
        ) ON COMMIT DROP
        """
    )
    op.execute(
        """
        INSERT INTO _e2_workspace (owner_id, project_id)
        SELECT owners.owner_id, gen_random_uuid()
          FROM (SELECT DISTINCT owner_id FROM documents) AS owners
        """
    )
    op.execute(
        """
        INSERT INTO projects (id, org_id, name, description, created_by, created_at, updated_at)
        SELECT w.project_id,
               u.org_id,
               'Workspace',
               'Documents that existed before projects did.',
               u.id,
               now(),
               now()
          FROM _e2_workspace w
          JOIN users u ON u.id = w.owner_id
        """
    )
    op.execute(
        """
        UPDATE documents d
           SET project_id = w.project_id,
               org_id     = u.org_id
          FROM _e2_workspace w
          JOIN users u ON u.id = w.owner_id
         WHERE d.owner_id = w.owner_id
        """
    )

    op.alter_column("documents", "org_id", nullable=False)
    op.alter_column("documents", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_documents_org", "documents", "organisations", ["org_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_documents_project", "documents", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )
    # Deleting a folder leaves its documents in the project root rather than
    # destroying them.
    op.create_foreign_key(
        "fk_documents_folder", "documents", "folders", ["folder_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_documents_org_id", "documents", ["org_id"])
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])


def _attach_file_assets_and_sessions() -> None:
    for table in ("file_assets", "chat_sessions"):
        op.add_column(table, sa.Column("org_id", sa.UUID(), nullable=True))
        op.add_column(table, sa.Column("project_id", sa.UUID(), nullable=True))
        op.execute(
            f"""
            UPDATE {table} t
               SET org_id = u.org_id
              FROM users u
             WHERE u.id = t.owner_id
            """
        )
        op.alter_column(table, "org_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_org", table, "organisations", ["org_id"], ["id"], ondelete="CASCADE"
        )
        op.create_foreign_key(
            f"fk_{table}_project", table, "projects", ["project_id"], ["id"], ondelete="SET NULL"
        )
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def _create_access_grants() -> None:
    op.create_table(
        "access_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        # Polymorphic by design: one grant table for three subject kinds keeps
        # the resolver a single query. subject_id therefore carries no foreign
        # key, and deleting a subject leaves an inert row behind — the resolver
        # joins through the real tables, so an orphan grants nothing.
        sa.Column("subject_type", _SUBJECT_ENUM, nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", _ROLE_ENUM, nullable=False),
        sa.Column("granted_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # One row per (subject, user): a re-grant updates the role in place.
        sa.UniqueConstraint("subject_type", "subject_id", "user_id", name="uq_access_grants_subject_user"),
    )
    op.create_index("ix_access_grants_user_id", "access_grants", ["user_id"])
    op.create_index("ix_access_grants_subject", "access_grants", ["subject_type", "subject_id"])


# ───────────────────────────────── backfill ───────────────────────────────


def _backfill_grants() -> None:
    # Every document's owner keeps owner access on that document.
    op.execute(
        """
        INSERT INTO access_grants
               (id, org_id, subject_type, subject_id, user_id, role, granted_by, created_at)
        SELECT gen_random_uuid(),
               d.org_id,
               'document'::aiper_subject,
               d.id,
               d.owner_id,
               'owner'::aiper_role,
               d.owner_id,
               now()
          FROM documents d
        ON CONFLICT ON CONSTRAINT uq_access_grants_subject_user DO NOTHING
        """
    )
    # ... and owner access on the Workspace project built for them above.
    op.execute(
        """
        INSERT INTO access_grants
               (id, org_id, subject_type, subject_id, user_id, role, granted_by, created_at)
        SELECT gen_random_uuid(),
               p.org_id,
               'project'::aiper_subject,
               p.id,
               p.created_by,
               'owner'::aiper_role,
               p.created_by,
               now()
          FROM projects p
         WHERE p.created_by IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_access_grants_subject_user DO NOTHING
        """
    )
    # Beyond the specified backfill: existing document shares become grants, so
    # that switching the document routes onto the resolver does not silently
    # revoke access somebody already had. Only shares already linked to an
    # account are migrated, and only within the document's organisation —
    # a share that crossed organisations is dropped, which is the new rule.
    op.execute(
        """
        INSERT INTO access_grants
               (id, org_id, subject_type, subject_id, user_id, role, granted_by, created_at)
        SELECT gen_random_uuid(),
               d.org_id,
               'document'::aiper_subject,
               d.id,
               c.user_id,
               (CASE WHEN c.role = 'viewer' THEN 'viewer' ELSE 'editor' END)::aiper_role,
               d.owner_id,
               now()
          FROM document_collaborators c
          JOIN documents d ON d.id = c.document_id
          JOIN users u     ON u.id = c.user_id
         WHERE c.user_id IS NOT NULL
           AND u.org_id = d.org_id
        ON CONFLICT ON CONSTRAINT uq_access_grants_subject_user DO NOTHING
        """
    )


# ──────────────────────────────── downgrade ───────────────────────────────


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS aiper_effective_access(uuid, aiper_subject, uuid)")

    op.drop_index("ix_access_grants_subject", table_name="access_grants")
    op.drop_index("ix_access_grants_user_id", table_name="access_grants")
    op.drop_table("access_grants")

    for table in ("file_assets", "chat_sessions"):
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        op.drop_constraint(f"fk_{table}_project", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_org", table, type_="foreignkey")
        op.drop_column(table, "project_id")
        op.drop_column(table, "org_id")

    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.drop_index("ix_documents_org_id", table_name="documents")
    op.drop_constraint("fk_documents_folder", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_project", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_org", "documents", type_="foreignkey")
    op.drop_column("documents", "folder_id")
    op.drop_column("documents", "project_id")
    op.drop_column("documents", "org_id")

    op.drop_index("ix_folders_parent_folder_id", table_name="folders")
    op.drop_index("ix_folders_project_id", table_name="folders")
    op.drop_table("folders")
    op.drop_index("ix_projects_org_id", table_name="projects")
    op.drop_table("projects")

    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_constraint("fk_users_org", "users", type_="foreignkey")
    op.drop_column("users", "org_id")

    op.drop_table("organisations")

    op.execute("DROP TYPE IF EXISTS aiper_role")
    op.execute("DROP TYPE IF EXISTS aiper_subject")
