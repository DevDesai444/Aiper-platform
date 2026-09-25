"""document_comments: threads anchored to a TipTap mark id

One row per posted comment. A thread is the set of rows that share
``(document_id, mark_id)``; the highlight itself is a TipTap ``comment`` mark
stored in ``documents.content_json``, keyed by the same ``mark_id``. Bodies
here, marks in the document — split for the reason spelled out in
``commentMark.ts``.

Denormalised ``org_id`` and ``project_id`` mirror ``documents``. The
effective-access resolver does not consult them (it walks the document
itself, and 0002's resolver already refuses a cross-org subject before any
grant is read); they exist so this table's own row-level-security policies
below have a cheap, redundant boundary check of their own — the same belt-
and-suspenders 0003 applies to ``documents``/``file_assets``.

Numbered ``0005`` per the platform's slot allocation; ``down_revision`` now
points at 0004 (E3's audit chain), the actual head as of this migration.

Row-level security. 0003 enables RLS and grants ``aiper_app`` privileges on
the tables that existed when IT ran, enumerated in a fixed dict — a table
created by a later migration is invisible to that dict, so without action
here ``aiper_app`` (the role the API actually connects as) would get a bare
"permission denied for table document_comments" the first time any comment
route ran outside a test that bypasses RLS. This migration carries its own
share of the same contract: GRANT, ENABLE/FORCE ROW LEVEL SECURITY, and one
policy per command, using the identical ``aiper_current_user_id()`` /
``aiper_effective_access`` primitives 0003 defined. The rules mirror the
routes exactly (see ``app/api/documents.py``):

  SELECT  viewer+ on the document
  INSERT  editor+ on the document, and the row's author must be the caller
  UPDATE  editor+ on the document (the only UPDATE path is resolve, which
          always stamps ``resolved_by_id`` to the caller)
  DELETE  the document's owner, or an editor deleting a comment they
          authored — the route loads a whole thread and 403s before issuing
          any DELETE unless every row qualifies, so the two layers enforce
          the same rule redundantly rather than one covering for the other.

No teardown of the RLS objects is needed in ``downgrade()``: policies and
grants are dependent on the table and Postgres drops both automatically when
the table itself is dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aiper_app"
_UID = "aiper_current_user_id()"
_UORG = f"aiper_user_org_id({_UID})"
_RESOLVER = "aiper_effective_access"


def _policy(table: str, command: str, *, using: str | None = None, check: str | None = None) -> None:
    """Same shape as 0003's local helper — kept self-contained here since
    migration modules do not import from one another."""
    name = f"{table}_{command.lower().replace(' ', '_')}"
    clauses = ""
    if using is not None:
        clauses += f" USING ({using})"
    if check is not None:
        clauses += f" WITH CHECK ({check})"
    op.execute(f"CREATE POLICY {name} ON {table} FOR {command} TO {_APP_ROLE}{clauses}")


def upgrade() -> None:
    op.create_table(
        "document_comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Client-generated on the editor side; a uuid string in practice, but
        # anything printable up to 64 chars is fine. Matched to the ``markId``
        # attribute of the TipTap ``comment`` mark.
        sa.Column("mark_id", sa.String(64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # Excerpt of the text under the mark at compose time. Snapshotted here
        # so the panel can render "quoted text" even after the underlying text
        # is edited (or deleted, orphaning the mark) — the DB is the record.
        sa.Column("quoted_text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Denormalised author display fields, matching ``revisions``: the
        # author's email/name at the moment they posted, immune to later renames
        # or account deletion.
        sa.Column("author_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("author_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The list query is always scoped by document, and threads sort by
    # ``(mark_id, created_at)``, so a compound index covers both.
    op.create_index(
        "ix_document_comments_document_mark",
        "document_comments",
        ["document_id", "mark_id", "created_at"],
    )
    # Reverse-lookup: everything a user authored, and an eventual "unresolved
    # comments across the org" view.
    op.create_index("ix_document_comments_author", "document_comments", ["author_id"])
    op.create_index("ix_document_comments_org", "document_comments", ["org_id"])

    # ── row-level security: this table's share of migration 0003's contract ──
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON document_comments TO {_APP_ROLE}")
    op.execute("ALTER TABLE document_comments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_comments FORCE ROW LEVEL SECURITY")

    editor_or_owner = f"{_RESOLVER}({_UID}, 'document', document_id) IN ('owner', 'editor')"
    is_owner = f"{_RESOLVER}({_UID}, 'document', document_id) = 'owner'"
    is_editor = f"{_RESOLVER}({_UID}, 'document', document_id) = 'editor'"

    _policy(
        "document_comments",
        "SELECT",
        using=f"org_id = {_UORG} AND {_RESOLVER}({_UID}, 'document', document_id) IS NOT NULL",
    )
    _policy(
        "document_comments",
        "INSERT",
        check=f"org_id = {_UORG} AND {editor_or_owner} AND author_id = {_UID}",
    )
    # The only UPDATE path is resolve: the route stamps resolved_by_id to the
    # caller in the same statement that requires them to hold editor+ access.
    _policy(
        "document_comments",
        "UPDATE",
        using=editor_or_owner,
        check=f"{editor_or_owner} AND resolved_by_id = {_UID}",
    )
    # Owner deletes any thread; a non-owner editor only a comment they wrote.
    _policy(
        "document_comments",
        "DELETE",
        using=f"org_id = {_UORG} AND ({is_owner} OR ({is_editor} AND author_id = {_UID}))",
    )


def downgrade() -> None:
    op.drop_index("ix_document_comments_org", table_name="document_comments")
    op.drop_index("ix_document_comments_author", table_name="document_comments")
    op.drop_index(
        "ix_document_comments_document_mark", table_name="document_comments"
    )
    op.drop_table("document_comments")
