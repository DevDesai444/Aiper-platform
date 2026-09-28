"""product_tree: nodes, interfaces, mates, parameters, dictionary, traceability

Seven new tables for Phase 1 of the hardware compatibility checker:
  parameter_definitions  — org-wide canonical parameter dictionary
  parameter_aliases      — folded-name aliases into that dictionary
  product_nodes          — self-referential BOM tree (multiple roots per project)
  node_interfaces        — typed ports (power/data/rf/thermal/mechanical/fluid)
  interface_mates        — unordered pairs of connected interfaces
  node_parameters        — parameter values on a node or interface
  node_documents         — node ↔ document traceability links

Plus one additive constraint on the existing `documents` table:
  UNIQUE (id, project_id) — required by the composite FK from node_documents

Design: docs/design/compatibility-checker.md §4.2, §4.4

RLS contract (per 0005 precedent): each table ships GRANT + ENABLE/FORCE RLS
+ one policy per command using aiper_current_user_id() / aiper_effective_access.
Immutability-by-omission: tables with no UPDATE route carry no UPDATE policy.
The grants lesson (hit 3× before): aiper_app is the role the API connects as;
every new table needs its own grant block or the first non-bypass request gets
"permission denied for table …".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aiper_app"
_UID = "aiper_current_user_id()"
_UORG = f"aiper_user_org_id({_UID})"
_RES = "aiper_effective_access"

_UUID = postgresql.UUID(as_uuid=True)


def _policy(
    table: str,
    command: str,
    *,
    using: str | None = None,
    check: str | None = None,
) -> None:
    name = f"{table}_{command.lower().replace(' ', '_')}"
    clauses = ""
    if using is not None:
        clauses += f" USING ({using})"
    if check is not None:
        clauses += f" WITH CHECK ({check})"
    op.execute(f"CREATE POLICY {name} ON {table} FOR {command} TO {_APP_ROLE}{clauses}")


def upgrade() -> None:
    # ── parameter_definitions ─────────────────────────────────────────────
    op.create_table(
        "parameter_definitions",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", _UUID, sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False, server_default="dimensionless"),
        sa.Column("canonical_unit", sa.Text(), nullable=False, server_default=""),
        sa.Column("criticality", sa.Text(), nullable=False, server_default="standard"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "key", name="uq_parameter_definitions_org_key"),
        sa.CheckConstraint("criticality IN ('standard','critical')", name="parameter_definitions_criticality"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON parameter_definitions TO {_APP_ROLE}")
    op.execute("ALTER TABLE parameter_definitions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE parameter_definitions FORCE ROW LEVEL SECURITY")
    _policy("parameter_definitions", "SELECT", using=f"org_id = {_UORG}")
    _policy("parameter_definitions", "INSERT", check=f"org_id = {_UORG} AND created_by = {_UID}")
    _policy("parameter_definitions", "UPDATE", using=f"org_id = {_UORG}", check=f"org_id = {_UORG}")

    # ── parameter_aliases ─────────────────────────────────────────────────
    op.create_table(
        "parameter_aliases",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", _UUID, sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("definition_id", _UUID, sa.ForeignKey("parameter_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "normalized_name", name="uq_parameter_aliases_org_name"),
        sa.CheckConstraint("source IN ('manual','ai_suggested')", name="parameter_aliases_source"),
    )
    op.create_index("ix_parameter_aliases_definition", "parameter_aliases", ["definition_id"])
    op.execute(f"GRANT SELECT, INSERT, DELETE ON parameter_aliases TO {_APP_ROLE}")
    op.execute("ALTER TABLE parameter_aliases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE parameter_aliases FORCE ROW LEVEL SECURITY")
    _policy("parameter_aliases", "SELECT", using=f"org_id = {_UORG}")
    _policy("parameter_aliases", "INSERT", check=f"org_id = {_UORG} AND created_by = {_UID}")
    _policy("parameter_aliases", "DELETE", using=f"org_id = {_UORG}")

    # ── product_nodes ─────────────────────────────────────────────────────
    op.create_table(
        "product_nodes",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", _UUID, sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_node_id", _UUID, nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("part_number", sa.Text(), nullable=True),
        sa.Column("supplier", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('assembly','subassembly','component','part')", name="product_nodes_kind"),
        sa.CheckConstraint("parent_node_id IS NULL OR parent_node_id <> id", name="product_nodes_no_self_parent"),
        sa.UniqueConstraint("id", "project_id", name="uq_product_nodes_id_project"),
    )
    # Composite FK: parent must be in the same project.
    op.execute(
        "ALTER TABLE product_nodes ADD CONSTRAINT fk_product_nodes_parent_project "
        "FOREIGN KEY (parent_node_id, project_id) "
        "REFERENCES product_nodes (id, project_id) ON DELETE CASCADE"
    )
    op.create_index("ix_product_nodes_project", "product_nodes", ["project_id"])
    op.create_index("ix_product_nodes_parent", "product_nodes", ["parent_node_id"])
    op.create_index(
        "ix_product_nodes_part_number",
        "product_nodes",
        ["part_number"],
        postgresql_where=sa.text("part_number IS NOT NULL"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON product_nodes TO {_APP_ROLE}")
    op.execute("ALTER TABLE product_nodes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE product_nodes FORCE ROW LEVEL SECURITY")
    _viewer = f"{_RES}({_UID}, 'project', project_id) IS NOT NULL"
    _editor = f"{_RES}({_UID}, 'project', project_id) IN ('owner','editor')"
    _policy("product_nodes", "SELECT", using=f"org_id = {_UORG} AND {_viewer}")
    _policy("product_nodes", "INSERT", check=f"org_id = {_UORG} AND {_editor} AND created_by = {_UID}")
    _policy("product_nodes", "UPDATE", using=f"org_id = {_UORG} AND {_editor}", check=f"org_id = {_UORG} AND {_editor}")
    _policy("product_nodes", "DELETE", using=f"org_id = {_UORG} AND {_editor}")

    # ── node_interfaces ───────────────────────────────────────────────────
    op.create_table(
        "node_interfaces",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", _UUID, sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", _UUID, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind IN ('power','data','rf','thermal','mechanical','fluid')",
            name="node_interfaces_kind",
        ),
        sa.UniqueConstraint("node_id", "name", name="uq_node_interfaces_node_name"),
        sa.UniqueConstraint("id", "project_id", name="uq_node_interfaces_id_project"),
        sa.UniqueConstraint("id", "node_id", name="uq_node_interfaces_id_node"),
    )
    op.execute(
        "ALTER TABLE node_interfaces ADD CONSTRAINT fk_node_interfaces_node_project "
        "FOREIGN KEY (node_id, project_id) "
        "REFERENCES product_nodes (id, project_id) ON DELETE CASCADE"
    )
    op.create_index("ix_node_interfaces_project", "node_interfaces", ["project_id"])
    op.create_index("ix_node_interfaces_node", "node_interfaces", ["node_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON node_interfaces TO {_APP_ROLE}")
    op.execute("ALTER TABLE node_interfaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE node_interfaces FORCE ROW LEVEL SECURITY")
    _ni_viewer = f"{_RES}({_UID}, 'project', project_id) IS NOT NULL"
    _ni_editor = f"{_RES}({_UID}, 'project', project_id) IN ('owner','editor')"
    _policy("node_interfaces", "SELECT", using=f"org_id = {_UORG} AND {_ni_viewer}")
    _policy("node_interfaces", "INSERT", check=f"org_id = {_UORG} AND {_ni_editor} AND created_by = {_UID}")
    _policy("node_interfaces", "UPDATE", using=f"org_id = {_UORG} AND {_ni_editor}", check=f"org_id = {_UORG} AND {_ni_editor}")
    _policy("node_interfaces", "DELETE", using=f"org_id = {_UORG} AND {_ni_editor}")

    # ── interface_mates ───────────────────────────────────────────────────
    op.create_table(
        "interface_mates",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", _UUID, sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interface_a_id", _UUID, nullable=False),
        sa.Column("interface_b_id", _UUID, nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("interface_a_id <> interface_b_id", name="interface_mates_no_self"),
        sa.CheckConstraint("interface_a_id < interface_b_id", name="interface_mates_ordered"),
        sa.UniqueConstraint("interface_a_id", "interface_b_id", name="uq_interface_mates_pair"),
    )
    op.execute(
        "ALTER TABLE interface_mates ADD CONSTRAINT fk_interface_mates_a "
        "FOREIGN KEY (interface_a_id, project_id) "
        "REFERENCES node_interfaces (id, project_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE interface_mates ADD CONSTRAINT fk_interface_mates_b "
        "FOREIGN KEY (interface_b_id, project_id) "
        "REFERENCES node_interfaces (id, project_id) ON DELETE CASCADE"
    )
    op.create_index("ix_interface_mates_project", "interface_mates", ["project_id"])
    op.execute(f"GRANT SELECT, INSERT, DELETE ON interface_mates TO {_APP_ROLE}")
    op.execute("ALTER TABLE interface_mates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE interface_mates FORCE ROW LEVEL SECURITY")
    _im_viewer = f"{_RES}({_UID}, 'project', project_id) IS NOT NULL"
    _im_editor = f"{_RES}({_UID}, 'project', project_id) IN ('owner','editor')"
    _policy("interface_mates", "SELECT", using=f"org_id = {_UORG} AND {_im_viewer}")
    _policy("interface_mates", "INSERT", check=f"org_id = {_UORG} AND {_im_editor} AND created_by = {_UID}")
    _policy("interface_mates", "DELETE", using=f"org_id = {_UORG} AND {_im_editor}")

    # ── node_parameters ───────────────────────────────────────────────────
    op.create_table(
        "node_parameters",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", _UUID, sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", _UUID, nullable=False),
        sa.Column("interface_id", _UUID, nullable=True),
        sa.Column("role", sa.Text(), nullable=False, server_default="bidirectional"),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("definition_id", _UUID, sa.ForeignKey("parameter_definitions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("value_kind", sa.Text(), nullable=False),
        sa.Column("value_num", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=False, server_default=""),
        sa.Column("tolerance_num", sa.Numeric(), nullable=True),
        sa.Column("min_num", sa.Numeric(), nullable=True),
        sa.Column("max_num", sa.Numeric(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_document_id", _UUID, sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_revision_number", sa.Integer(), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('supply','accept','bidirectional')", name="node_parameters_role"),
        sa.CheckConstraint(
            "(value_kind = 'quantity' AND value_num IS NOT NULL AND value_text = '') "
            "OR (value_kind = 'text' AND value_text <> '' AND value_num IS NULL "
            "AND tolerance_num IS NULL AND min_num IS NULL AND max_num IS NULL)",
            name="node_parameters_value_shape",
        ),
        sa.CheckConstraint("tolerance_num IS NULL OR tolerance_num >= 0", name="node_parameters_tolerance_nonneg"),
        sa.CheckConstraint("min_num IS NULL OR max_num IS NULL OR min_num <= max_num", name="node_parameters_envelope_sane"),
        sa.CheckConstraint(
            "interface_id IS NOT NULL OR role = 'bidirectional'",
            name="node_parameters_role_scope",
        ),
        sa.CheckConstraint("value_kind IN ('quantity','text')", name="node_parameters_value_kind"),
    )
    op.execute(
        "ALTER TABLE node_parameters ADD CONSTRAINT fk_node_parameters_node_project "
        "FOREIGN KEY (node_id, project_id) "
        "REFERENCES product_nodes (id, project_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE node_parameters ADD CONSTRAINT fk_node_parameters_interface_node "
        "FOREIGN KEY (interface_id, node_id) "
        "REFERENCES node_interfaces (id, node_id) ON DELETE CASCADE"
    )
    op.create_index(
        "uq_node_parameters_node_name",
        "node_parameters",
        ["node_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("interface_id IS NULL"),
    )
    op.create_index(
        "uq_node_parameters_interface_name",
        "node_parameters",
        ["interface_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("interface_id IS NOT NULL"),
    )
    op.create_index("ix_node_parameters_project", "node_parameters", ["project_id"])
    op.create_index("ix_node_parameters_definition", "node_parameters", ["definition_id"])
    op.create_index("ix_node_parameters_source_doc", "node_parameters", ["source_document_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON node_parameters TO {_APP_ROLE}")
    op.execute("ALTER TABLE node_parameters ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE node_parameters FORCE ROW LEVEL SECURITY")
    _np_viewer = f"{_RES}({_UID}, 'project', project_id) IS NOT NULL"
    _np_editor = f"{_RES}({_UID}, 'project', project_id) IN ('owner','editor')"
    _policy("node_parameters", "SELECT", using=f"org_id = {_UORG} AND {_np_viewer}")
    _policy("node_parameters", "INSERT", check=f"org_id = {_UORG} AND {_np_editor} AND created_by = {_UID}")
    _policy("node_parameters", "UPDATE", using=f"org_id = {_UORG} AND {_np_editor}", check=f"org_id = {_UORG} AND {_np_editor}")
    _policy("node_parameters", "DELETE", using=f"org_id = {_UORG} AND {_np_editor}")

    # ── node_documents ────────────────────────────────────────────────────
    # Composite FK on documents requires UNIQUE (id, project_id) on documents.
    op.execute("ALTER TABLE documents ADD CONSTRAINT uq_documents_id_project UNIQUE (id, project_id)")
    op.create_table(
        "node_documents",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", _UUID, sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", _UUID, nullable=False),
        sa.Column("document_id", _UUID, nullable=False),
        sa.Column("relation", sa.Text(), nullable=False, server_default="reference"),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "relation IN ('reference','design-spec','test-report','sign-off','requirement')",
            name="node_documents_relation",
        ),
        sa.UniqueConstraint("node_id", "document_id", "relation", name="uq_node_documents"),
    )
    op.execute(
        "ALTER TABLE node_documents ADD CONSTRAINT fk_node_documents_node_project "
        "FOREIGN KEY (node_id, project_id) "
        "REFERENCES product_nodes (id, project_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE node_documents ADD CONSTRAINT fk_node_documents_document_project "
        "FOREIGN KEY (document_id, project_id) "
        "REFERENCES documents (id, project_id) ON DELETE CASCADE"
    )
    op.create_index("ix_node_documents_document", "node_documents", ["document_id"])
    op.execute(f"GRANT SELECT, INSERT, DELETE ON node_documents TO {_APP_ROLE}")
    op.execute("ALTER TABLE node_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE node_documents FORCE ROW LEVEL SECURITY")
    _nd_viewer = f"{_RES}({_UID}, 'project', project_id) IS NOT NULL"
    _nd_editor = f"{_RES}({_UID}, 'project', project_id) IN ('owner','editor')"
    _policy("node_documents", "SELECT", using=f"org_id = {_UORG} AND {_nd_viewer}")
    _policy("node_documents", "INSERT", check=f"org_id = {_UORG} AND {_nd_editor} AND created_by = {_UID}")
    _policy("node_documents", "DELETE", using=f"org_id = {_UORG} AND {_nd_editor}")


def downgrade() -> None:
    op.drop_index("ix_node_documents_document", table_name="node_documents")
    op.drop_table("node_documents")
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS uq_documents_id_project")

    op.drop_index("ix_node_parameters_source_doc", table_name="node_parameters")
    op.drop_index("ix_node_parameters_definition", table_name="node_parameters")
    op.drop_index("ix_node_parameters_project", table_name="node_parameters")
    op.drop_index("uq_node_parameters_interface_name", table_name="node_parameters")
    op.drop_index("uq_node_parameters_node_name", table_name="node_parameters")
    op.drop_table("node_parameters")

    op.drop_index("ix_interface_mates_project", table_name="interface_mates")
    op.drop_table("interface_mates")

    op.drop_index("ix_node_interfaces_node", table_name="node_interfaces")
    op.drop_index("ix_node_interfaces_project", table_name="node_interfaces")
    op.drop_table("node_interfaces")

    op.drop_index("ix_product_nodes_part_number", table_name="product_nodes")
    op.drop_index("ix_product_nodes_parent", table_name="product_nodes")
    op.drop_index("ix_product_nodes_project", table_name="product_nodes")
    op.drop_table("product_nodes")

    op.drop_index("ix_parameter_aliases_definition", table_name="parameter_aliases")
    op.drop_table("parameter_aliases")

    op.drop_table("parameter_definitions")
