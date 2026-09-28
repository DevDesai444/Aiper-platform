"""Product tree CRUD — nodes, interfaces, mates, parameters, dictionary, traceability.

Phase 1 of the hardware compatibility checker (design §13).  No findings
engine yet (Phase 2).  Access model: project-scoped, same resolver as every
other route (aiper_effective_access).

Route auth summary
  read  (GET)  → viewer+ on the project
  write (POST/PUT/DELETE) → editor+ on the project
  dictionary writes → viewer+ on the org (wiki-style, D3): any org member
    who can see the project can edit parameter definitions / aliases.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, text

from app import schemas
from app.compat.normalize import fold
from app.compat.units import lookup as unit_lookup
from app.core.deps import CurrentUser, DbSession
from app.db.models import (
    Document,
    InterfaceMate,
    NodeDocument,
    NodeInterface,
    NodeParameter,
    ParameterAlias,
    ParameterDefinition,
    ProductNode,
)
from app.services import audit
from app.services.permissions import require_access

router = APIRouter(tags=["product-tree"])


# ─────────────────────────── helpers ──────────────────────────────────────────


async def _require_project_viewer(db: DbSession, user: CurrentUser, project_id: uuid.UUID) -> None:
    await require_access(db, user, "project", project_id, "viewer")


async def _require_project_editor(db: DbSession, user: CurrentUser, project_id: uuid.UUID) -> None:
    await require_access(db, user, "project", project_id, "editor")


async def _get_node_or_404(
    db: DbSession, project_id: uuid.UUID, node_id: uuid.UUID
) -> ProductNode:
    row = (
        await db.execute(
            select(ProductNode).where(
                ProductNode.id == node_id,
                ProductNode.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    return row


async def _get_interface_or_404(
    db: DbSession, project_id: uuid.UUID, interface_id: uuid.UUID
) -> NodeInterface:
    row = (
        await db.execute(
            select(NodeInterface).where(
                NodeInterface.id == interface_id,
                NodeInterface.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interface not found")
    return row


async def _get_parameter_or_404(
    db: DbSession, project_id: uuid.UUID, param_id: uuid.UUID
) -> NodeParameter:
    row = (
        await db.execute(
            select(NodeParameter).where(
                NodeParameter.id == param_id,
                NodeParameter.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parameter not found")
    return row


async def _node_would_cycle(
    db: DbSession, node_id: uuid.UUID, new_parent_id: uuid.UUID
) -> bool:
    """True if placing node_id under new_parent_id creates a cycle."""
    current: uuid.UUID | None = new_parent_id
    for _ in range(50):
        if current == node_id:
            return True
        if current is None:
            return False
        current = (
            await db.execute(
                select(ProductNode.parent_node_id).where(ProductNode.id == current)
            )
        ).scalar_one_or_none()
    return True


async def _resolve_definition(
    db: DbSession, org_id: uuid.UUID, normalized_name: str
) -> uuid.UUID | None:
    """Look up a definition_id for a folded name (canonical key or alias)."""
    # Direct canonical key match
    row = (
        await db.execute(
            select(ParameterDefinition.id).where(
                ParameterDefinition.org_id == org_id,
                ParameterDefinition.key == normalized_name,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    # Alias match
    alias = (
        await db.execute(
            select(ParameterAlias.definition_id).where(
                ParameterAlias.org_id == org_id,
                ParameterAlias.normalized_name == normalized_name,
            )
        )
    ).scalar_one_or_none()
    return alias


# ─────────────────────────── parameter definitions ────────────────────────────


@router.get("/orgs/{org_id}/parameter-definitions", response_model=list[schemas.ParameterDefinitionOut])
async def list_parameter_definitions(
    org_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    """List all parameter definitions for the org."""
    if user.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    rows = (
        await db.execute(
            select(ParameterDefinition)
            .where(ParameterDefinition.org_id == org_id)
            .order_by(ParameterDefinition.key)
        )
    ).scalars().all()
    return rows


@router.post("/orgs/{org_id}/parameter-definitions", response_model=schemas.ParameterDefinitionOut, status_code=201)
async def create_parameter_definition(
    org_id: uuid.UUID,
    body: schemas.ParameterDefinitionCreate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    """Create a parameter definition (wiki-style: any org member)."""
    if user.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    normalized_key = fold(body.key)
    defn = ParameterDefinition(
        org_id=org_id,
        key=normalized_key,
        display_name=body.display_name,
        dimension=body.dimension,
        canonical_unit=body.canonical_unit,
        criticality=body.criticality,
        description=body.description,
        created_by=user.id,
    )
    db.add(defn)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=org_id,
        actor_id=user.id,
        action=audit.PARAMDEF_CREATE,
        subject_type="parameter_definition",
        subject_id=defn.id,
        payload={"key": normalized_key, "dimension": body.dimension},
    )
    await db.commit()
    await db.refresh(defn)
    return defn


@router.put("/orgs/{org_id}/parameter-definitions/{definition_id}", response_model=schemas.ParameterDefinitionOut)
async def update_parameter_definition(
    org_id: uuid.UUID,
    definition_id: uuid.UUID,
    body: schemas.ParameterDefinitionUpdate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    if user.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    defn = (
        await db.execute(
            select(ParameterDefinition).where(
                ParameterDefinition.id == definition_id,
                ParameterDefinition.org_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if defn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    updates: dict[str, Any] = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
        defn.display_name = body.display_name
    if body.dimension is not None:
        updates["dimension"] = body.dimension
        defn.dimension = body.dimension
    if body.canonical_unit is not None:
        updates["canonical_unit"] = body.canonical_unit
        defn.canonical_unit = body.canonical_unit
    if body.criticality is not None:
        updates["criticality"] = body.criticality
        defn.criticality = body.criticality
    if body.description is not None:
        updates["description"] = body.description
        defn.description = body.description
    await db.flush()
    await audit.record_audit(
        db,
        org_id=org_id,
        actor_id=user.id,
        action=audit.PARAMDEF_UPDATE,
        subject_type="parameter_definition",
        subject_id=defn.id,
        payload=updates,
    )
    await db.commit()
    await db.refresh(defn)
    return defn


@router.get("/orgs/{org_id}/parameter-definitions/{definition_id}/aliases", response_model=list[schemas.AliasOut])
async def list_aliases(
    org_id: uuid.UUID,
    definition_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    if user.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    rows = (
        await db.execute(
            select(ParameterAlias).where(
                ParameterAlias.org_id == org_id,
                ParameterAlias.definition_id == definition_id,
            )
        )
    ).scalars().all()
    return rows


@router.post("/orgs/{org_id}/parameter-definitions/{definition_id}/aliases", response_model=schemas.AliasOut, status_code=201)
async def create_alias(
    org_id: uuid.UUID,
    definition_id: uuid.UUID,
    body: schemas.AliasCreate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    if user.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    normalized = fold(body.normalized_name)
    alias = ParameterAlias(
        org_id=org_id,
        definition_id=definition_id,
        normalized_name=normalized,
        source=body.source,
        created_by=user.id,
    )
    db.add(alias)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=org_id,
        actor_id=user.id,
        action=audit.ALIAS_CREATE,
        subject_type="parameter_alias",
        subject_id=alias.id,
        payload={"normalized_name": normalized, "source": body.source},
    )
    await db.commit()
    await db.refresh(alias)
    return alias


@router.delete("/orgs/{org_id}/parameter-definitions/{definition_id}/aliases/{alias_id}", status_code=204)
async def delete_alias(
    org_id: uuid.UUID,
    definition_id: uuid.UUID,
    alias_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> None:
    if user.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    alias = (
        await db.execute(
            select(ParameterAlias).where(
                ParameterAlias.id == alias_id,
                ParameterAlias.org_id == org_id,
                ParameterAlias.definition_id == definition_id,
            )
        )
    ).scalar_one_or_none()
    if alias is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await audit.record_audit(
        db,
        org_id=org_id,
        actor_id=user.id,
        action=audit.ALIAS_DELETE,
        subject_type="parameter_alias",
        subject_id=alias.id,
        payload={"normalized_name": alias.normalized_name},
    )
    await db.delete(alias)
    await db.commit()


# ─────────────────────────── product nodes ────────────────────────────────────


@router.get("/projects/{project_id}/nodes", response_model=schemas.NodeTree)
async def list_nodes(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_viewer(db, user, project_id)
    nodes = (
        await db.execute(
            select(ProductNode)
            .where(ProductNode.project_id == project_id)
            .order_by(ProductNode.name)
        )
    ).scalars().all()
    return schemas.NodeTree(nodes=list(nodes))


@router.post("/projects/{project_id}/nodes", response_model=schemas.NodeOut, status_code=201)
async def create_node(
    project_id: uuid.UUID,
    body: schemas.NodeCreate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)
    # Verify parent is in this project
    if body.parent_node_id is not None:
        parent = await _get_node_or_404(db, project_id, body.parent_node_id)
        org_id = parent.org_id
    else:
        from app.db.models import Project
        proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
        if proj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        org_id = proj.org_id

    node = ProductNode(
        org_id=org_id,
        project_id=project_id,
        parent_node_id=body.parent_node_id,
        kind=body.kind,
        name=body.name,
        part_number=body.part_number,
        supplier=body.supplier,
        description=body.description,
        attributes=body.attributes,
        created_by=user.id,
    )
    db.add(node)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=org_id,
        actor_id=user.id,
        action=audit.NODE_CREATE,
        subject_type="product_node",
        subject_id=node.id,
        payload={"name": body.name, "kind": body.kind, "parent_node_id": str(body.parent_node_id) if body.parent_node_id else None},
    )
    await db.commit()
    await db.refresh(node)
    return node


@router.get("/projects/{project_id}/nodes/{node_id}", response_model=schemas.NodeOut)
async def get_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_viewer(db, user, project_id)
    return await _get_node_or_404(db, project_id, node_id)


@router.put("/projects/{project_id}/nodes/{node_id}", response_model=schemas.NodeOut)
async def update_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: schemas.NodeUpdate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)
    node = await _get_node_or_404(db, project_id, node_id)
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
        node.name = body.name
    if body.kind is not None:
        updates["kind"] = body.kind
        node.kind = body.kind
    if body.part_number is not None:
        updates["part_number"] = body.part_number
        node.part_number = body.part_number
    if body.supplier is not None:
        updates["supplier"] = body.supplier
        node.supplier = body.supplier
    if body.description is not None:
        updates["description"] = body.description
        node.description = body.description
    if body.attributes is not None:
        updates["attributes"] = body.attributes
        node.attributes = body.attributes
    await db.flush()
    await audit.record_audit(
        db,
        org_id=node.org_id,
        actor_id=user.id,
        action=audit.NODE_UPDATE,
        subject_type="product_node",
        subject_id=node.id,
        payload=updates,
    )
    await db.commit()
    await db.refresh(node)
    return node


@router.post("/projects/{project_id}/nodes/{node_id}/move", response_model=schemas.NodeOut)
async def move_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: schemas.NodeMove,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)
    node = await _get_node_or_404(db, project_id, node_id)
    new_parent = body.parent_node_id

    if new_parent is not None:
        # Verify new parent is in this project
        await _get_node_or_404(db, project_id, new_parent)
        # Cycle guard
        if new_parent == node_id or await _node_would_cycle(db, node_id, new_parent):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Move would create a cycle")

    old_parent = node.parent_node_id
    node.parent_node_id = new_parent
    await db.flush()
    await audit.record_audit(
        db,
        org_id=node.org_id,
        actor_id=user.id,
        action=audit.NODE_MOVE,
        subject_type="product_node",
        subject_id=node.id,
        payload={"from": str(old_parent) if old_parent else None, "to": str(new_parent) if new_parent else None},
    )
    await db.commit()
    await db.refresh(node)
    return node


@router.delete("/projects/{project_id}/nodes/{node_id}", status_code=204)
async def delete_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> None:
    await _require_project_editor(db, user, project_id)
    node = await _get_node_or_404(db, project_id, node_id)
    await audit.record_audit(
        db,
        org_id=node.org_id,
        actor_id=user.id,
        action=audit.NODE_DELETE,
        subject_type="product_node",
        subject_id=node.id,
        payload={"name": node.name},
    )
    await db.delete(node)
    await db.commit()


# ─────────────────────────── interfaces ───────────────────────────────────────


@router.get("/projects/{project_id}/nodes/{node_id}/interfaces", response_model=list[schemas.InterfaceOut])
async def list_interfaces(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_viewer(db, user, project_id)
    await _get_node_or_404(db, project_id, node_id)
    rows = (
        await db.execute(
            select(NodeInterface)
            .where(NodeInterface.project_id == project_id, NodeInterface.node_id == node_id)
            .order_by(NodeInterface.name)
        )
    ).scalars().all()
    return list(rows)


@router.post("/projects/{project_id}/nodes/{node_id}/interfaces", response_model=schemas.InterfaceOut, status_code=201)
async def create_interface(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: schemas.InterfaceCreate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)
    node = await _get_node_or_404(db, project_id, node_id)
    iface = NodeInterface(
        org_id=node.org_id,
        project_id=project_id,
        node_id=node_id,
        kind=body.kind,
        name=body.name,
        description=body.description,
        created_by=user.id,
    )
    db.add(iface)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=node.org_id,
        actor_id=user.id,
        action=audit.INTERFACE_CREATE,
        subject_type="node_interface",
        subject_id=iface.id,
        payload={"name": body.name, "kind": body.kind, "node_id": str(node_id)},
    )
    await db.commit()
    await db.refresh(iface)
    return iface


@router.put("/projects/{project_id}/nodes/{node_id}/interfaces/{interface_id}", response_model=schemas.InterfaceOut)
async def update_interface(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    interface_id: uuid.UUID,
    body: schemas.InterfaceUpdate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)
    iface = await _get_interface_or_404(db, project_id, interface_id)
    if iface.node_id != node_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
        iface.name = body.name
    if body.kind is not None:
        updates["kind"] = body.kind
        iface.kind = body.kind
    if body.description is not None:
        updates["description"] = body.description
        iface.description = body.description
    await db.flush()
    await audit.record_audit(
        db,
        org_id=iface.org_id,
        actor_id=user.id,
        action=audit.INTERFACE_UPDATE,
        subject_type="node_interface",
        subject_id=iface.id,
        payload=updates,
    )
    await db.commit()
    await db.refresh(iface)
    return iface


@router.delete("/projects/{project_id}/nodes/{node_id}/interfaces/{interface_id}", status_code=204)
async def delete_interface(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    interface_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> None:
    await _require_project_editor(db, user, project_id)
    iface = await _get_interface_or_404(db, project_id, interface_id)
    if iface.node_id != node_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await audit.record_audit(
        db,
        org_id=iface.org_id,
        actor_id=user.id,
        action=audit.INTERFACE_DELETE,
        subject_type="node_interface",
        subject_id=iface.id,
        payload={"name": iface.name},
    )
    await db.delete(iface)
    await db.commit()


# ─────────────────────────── interface mates ──────────────────────────────────


@router.get("/projects/{project_id}/mates", response_model=list[schemas.MateOut])
async def list_mates(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_viewer(db, user, project_id)
    rows = (
        await db.execute(
            select(InterfaceMate).where(InterfaceMate.project_id == project_id)
        )
    ).scalars().all()
    return list(rows)


@router.post("/projects/{project_id}/mates", response_model=schemas.MateOut, status_code=201)
async def create_mate(
    project_id: uuid.UUID,
    body: schemas.MateCreate,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)

    # Resolve both interfaces and get org_id
    iface_a = await _get_interface_or_404(db, project_id, body.interface_a_id)
    await _get_interface_or_404(db, project_id, body.interface_b_id)

    if body.interface_a_id == body.interface_b_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Cannot mate an interface to itself")

    # Canonical order: smaller UUID first
    a_id, b_id = sorted([body.interface_a_id, body.interface_b_id])

    mate = InterfaceMate(
        org_id=iface_a.org_id,
        project_id=project_id,
        interface_a_id=a_id,
        interface_b_id=b_id,
        note=body.note,
        created_by=user.id,
    )
    db.add(mate)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=iface_a.org_id,
        actor_id=user.id,
        action=audit.MATE_CREATE,
        subject_type="interface_mate",
        subject_id=mate.id,
        payload={"interface_a_id": str(a_id), "interface_b_id": str(b_id)},
    )
    await db.commit()
    await db.refresh(mate)
    return mate


@router.delete("/projects/{project_id}/mates/{mate_id}", status_code=204)
async def delete_mate(
    project_id: uuid.UUID,
    mate_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> None:
    await _require_project_editor(db, user, project_id)
    mate = (
        await db.execute(
            select(InterfaceMate).where(
                InterfaceMate.id == mate_id,
                InterfaceMate.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if mate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await audit.record_audit(
        db,
        org_id=mate.org_id,
        actor_id=user.id,
        action=audit.MATE_DELETE,
        subject_type="interface_mate",
        subject_id=mate.id,
        payload={},
    )
    await db.delete(mate)
    await db.commit()


# ─────────────────────────── parameters ───────────────────────────────────────


@router.get("/projects/{project_id}/nodes/{node_id}/parameters", response_model=list[schemas.ParameterOut])
async def list_parameters(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_viewer(db, user, project_id)
    await _get_node_or_404(db, project_id, node_id)
    rows = (
        await db.execute(
            select(NodeParameter).where(
                NodeParameter.project_id == project_id,
                NodeParameter.node_id == node_id,
            )
        )
    ).scalars().all()
    return list(rows)


@router.post("/projects/{project_id}/nodes/{node_id}/parameters", response_model=schemas.ParameterOut, status_code=201)
async def set_parameter(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: schemas.ParameterSet,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)
    node = await _get_node_or_404(db, project_id, node_id)

    # Validate interface belongs to this node
    if body.interface_id is not None:
        iface = await _get_interface_or_404(db, project_id, body.interface_id)
        if iface.node_id != node_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Interface does not belong to this node")

    # role_scope: node-level params must be bidirectional
    if body.interface_id is None and body.role != "bidirectional":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Node-level parameters must have role=bidirectional")

    # Validate unit if quantity
    if body.value_kind == "quantity" and body.unit:
        if unit_lookup(body.unit) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown unit '{body.unit}'")

    normalized = fold(body.raw_name)
    definition_id = await _resolve_definition(db, node.org_id, normalized)

    # Check for existing param with same name/scope and update it
    existing = (
        await db.execute(
            select(NodeParameter).where(
                NodeParameter.node_id == node_id,
                NodeParameter.normalized_name == normalized,
                NodeParameter.interface_id == body.interface_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        old_payload = {
            "value_num": float(existing.value_num) if existing.value_num is not None else None,
            "unit": existing.unit,
            "tolerance_num": float(existing.tolerance_num) if existing.tolerance_num is not None else None,
            "value_text": existing.value_text,
        }
        existing.role = body.role
        existing.raw_name = body.raw_name
        existing.definition_id = definition_id
        existing.value_kind = body.value_kind
        existing.value_num = body.value_num
        existing.unit = body.unit
        existing.tolerance_num = body.tolerance_num
        existing.min_num = body.min_num
        existing.max_num = body.max_num
        existing.value_text = body.value_text
        existing.source_document_id = body.source_document_id
        existing.source_revision_number = body.source_revision_number
        existing.source_quote = body.source_quote
        existing.note = body.note
        param = existing
        await db.flush()
        new_payload = {
            "value_num": body.value_num,
            "unit": body.unit,
            "tolerance_num": body.tolerance_num,
            "value_text": body.value_text,
        }
        await audit.record_audit(
            db,
            org_id=node.org_id,
            actor_id=user.id,
            action=audit.PARAM_SET,
            subject_type="node_parameter",
            subject_id=param.id,
            payload={"old": old_payload, "new": new_payload, "raw_name": body.raw_name},
        )
    else:
        param = NodeParameter(
            org_id=node.org_id,
            project_id=project_id,
            node_id=node_id,
            interface_id=body.interface_id,
            role=body.role,
            raw_name=body.raw_name,
            normalized_name=normalized,
            definition_id=definition_id,
            value_kind=body.value_kind,
            value_num=body.value_num,
            unit=body.unit,
            tolerance_num=body.tolerance_num,
            min_num=body.min_num,
            max_num=body.max_num,
            value_text=body.value_text,
            source_document_id=body.source_document_id,
            source_revision_number=body.source_revision_number,
            source_quote=body.source_quote,
            note=body.note,
            created_by=user.id,
        )
        db.add(param)
        await db.flush()
        await audit.record_audit(
            db,
            org_id=node.org_id,
            actor_id=user.id,
            action=audit.PARAM_SET,
            subject_type="node_parameter",
            subject_id=param.id,
            payload={"old": None, "new": {"value_num": body.value_num, "unit": body.unit, "value_text": body.value_text}, "raw_name": body.raw_name},
        )

    await db.commit()
    await db.refresh(param)
    return param


@router.delete("/projects/{project_id}/nodes/{node_id}/parameters/{param_id}", status_code=204)
async def delete_parameter(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    param_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> None:
    await _require_project_editor(db, user, project_id)
    param = await _get_parameter_or_404(db, project_id, param_id)
    if param.node_id != node_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await audit.record_audit(
        db,
        org_id=param.org_id,
        actor_id=user.id,
        action=audit.PARAM_DELETE,
        subject_type="node_parameter",
        subject_id=param.id,
        payload={"raw_name": param.raw_name},
    )
    await db.delete(param)
    await db.commit()


# ─────────────────────────── node documents ───────────────────────────────────


@router.get("/projects/{project_id}/nodes/{node_id}/documents", response_model=list[schemas.NodeDocumentOut])
async def list_node_documents(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_viewer(db, user, project_id)
    await _get_node_or_404(db, project_id, node_id)
    rows = (
        await db.execute(
            select(NodeDocument).where(
                NodeDocument.project_id == project_id,
                NodeDocument.node_id == node_id,
            )
        )
    ).scalars().all()
    return list(rows)


@router.post("/projects/{project_id}/nodes/{node_id}/documents", response_model=schemas.NodeDocumentOut, status_code=201)
async def link_node_document(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: schemas.NodeDocumentLink,
    db: DbSession,
    user: CurrentUser,
) -> Any:
    await _require_project_editor(db, user, project_id)
    node = await _get_node_or_404(db, project_id, node_id)

    # Verify the document belongs to this project
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == body.document_id,
                Document.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found in this project")

    link = NodeDocument(
        org_id=node.org_id,
        project_id=project_id,
        node_id=node_id,
        document_id=body.document_id,
        relation=body.relation,
        created_by=user.id,
    )
    db.add(link)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=node.org_id,
        actor_id=user.id,
        action=audit.NODEDOC_LINK,
        subject_type="node_document",
        subject_id=link.id,
        payload={"document_id": str(body.document_id), "relation": body.relation},
    )
    await db.commit()
    await db.refresh(link)
    return link


@router.delete("/projects/{project_id}/nodes/{node_id}/documents/{link_id}", status_code=204)
async def unlink_node_document(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    link_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> None:
    await _require_project_editor(db, user, project_id)
    link = (
        await db.execute(
            select(NodeDocument).where(
                NodeDocument.id == link_id,
                NodeDocument.project_id == project_id,
                NodeDocument.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await audit.record_audit(
        db,
        org_id=link.org_id,
        actor_id=user.id,
        action=audit.NODEDOC_UNLINK,
        subject_type="node_document",
        subject_id=link.id,
        payload={"document_id": str(link.document_id)},
    )
    await db.delete(link)
    await db.commit()
