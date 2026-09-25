"""document_comments: threads anchored to a TipTap mark id

One row per posted comment. A thread is the set of rows that share
``(document_id, mark_id)``; the highlight itself is a TipTap ``comment`` mark
stored in ``documents.content_json``, keyed by the same ``mark_id``. Bodies
here, marks in the document — split for the reason spelled out in
``commentMark.ts``.

Denormalised ``org_id`` and ``project_id`` mirror ``documents``. They are not
consulted by the effective-access resolver (which walks the document itself),
but they are the axes RLS policies key off — leaving them here keeps this table
forward-compatible without a follow-up migration.

Numbered ``0005`` per the platform's slot allocation (E3 owns ``0003``/``0004``);
until those land this migration links directly to ``0002``. If they land first
the ``down_revision`` here needs to point at their head — see PR body.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def downgrade() -> None:
    op.drop_index("ix_document_comments_org", table_name="document_comments")
    op.drop_index("ix_document_comments_author", table_name="document_comments")
    op.drop_index(
        "ix_document_comments_document_mark", table_name="document_comments"
    )
    op.drop_table("document_comments")
