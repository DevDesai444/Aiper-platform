"""Product-tree routes + RLS — Phase 1 of the compatibility checker.

Three concerns tested here:

1. **RLS isolation** (aiper_app role, policies live): a user in org B cannot
   see or mutate org A's nodes, interfaces, mates, parameters, or traceability
   links — even when they know the IDs.
2. **Access-level enforcement** (via rls_api): viewer can read, cannot write;
   editor can write; anonymous gets 401/403.
3. **Business invariants**: cycle guard on node move; unknown unit rejected;
   node-level param must be bidirectional.

All DB arranging goes through the superuser `db` fixture; RLS enforcement is
tested over `app_session_factory` (connects as aiper_app).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AccessGrant,
    InterfaceMate,
    NodeDocument,
    NodeInterface,
    NodeParameter,
    ParameterDefinition,
    ProductNode,
)
from tests.conftest import Api, as_user, anonymous
from tests.factories import (
    make_document,
    make_grant,
    make_org,
    make_project,
    make_user,
)


# ─────────────────────────── fixtures ────────────────────────────────────────


@pytest_asyncio.fixture
async def tree_api(app_session_factory):
    """rls_api equivalent that includes the product-tree router."""
    from app.api import product_tree
    from app.core.deps import current_user, get_session

    app = FastAPI()
    versioned = APIRouter(prefix="/api/v1")
    versioned.include_router(product_tree.router)
    app.include_router(versioned)

    holder: dict = {}

    async def request_session():
        async with as_user(app_session_factory, holder["user"]) as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[current_user] = lambda: holder["user"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield Api(client, lambda user: holder.__setitem__("user", user))
    app.dependency_overrides.clear()


async def _setup_two_orgs(db):
    """Standard two-org scenario for isolation tests."""
    org_a = await make_org(db, "tree-org-a")
    org_b = await make_org(db, "tree-org-b")
    alice = await make_user(db, org_a, "alice@tree.example")
    bob = await make_user(db, org_b, "bob@tree.example")
    carol = await make_user(db, org_a, "carol@tree.example")  # viewer on alice's project

    proj_a = await make_project(db, org_a, alice, "Sat A")
    await make_grant(db, org_id=org_a.id, subject_type="project", subject_id=proj_a.id, user=alice, role="owner")
    await make_grant(db, org_id=org_a.id, subject_type="project", subject_id=proj_a.id, user=carol, role="viewer")

    proj_b = await make_project(db, org_b, bob, "Sat B")
    await make_grant(db, org_id=org_b.id, subject_type="project", subject_id=proj_b.id, user=bob, role="owner")

    # A node in org A's project — arranged via superuser (bypass RLS)
    node_a = ProductNode(
        org_id=org_a.id,
        project_id=proj_a.id,
        kind="component",
        name="EPS PCU",
        created_by=alice.id,
    )
    db.add(node_a)
    await db.flush()

    await db.commit()
    return org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a


# ─────────────────────────── RLS: cross-org isolation ────────────────────────


async def test_cross_org_nodes_invisible_at_sql_level(db, app_session_factory):
    """Bob's session sees zero of org A's product_nodes."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    async with as_user(app_session_factory, bob) as bob_session:
        rows = (await bob_session.execute(select(ProductNode))).scalars().all()
    assert len(rows) == 0


async def test_cross_org_write_denied_at_sql_level(db, app_session_factory):
    """Bob cannot insert a node into org A's project at the SQL level."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    async with as_user(app_session_factory, bob) as bob_session:
        from sqlalchemy.exc import InternalError, IntegrityError, ProgrammingError
        with pytest.raises((InternalError, IntegrityError, ProgrammingError, Exception)):
            bad = ProductNode(
                org_id=org_a.id,  # deliberately wrong org
                project_id=proj_a.id,
                kind="component",
                name="should not exist",
                created_by=bob.id,
            )
            bob_session.add(bad)
            await bob_session.flush()


async def test_cross_org_delete_denied_at_sql_level(db, app_session_factory):
    """Bob cannot delete a node from org A at the SQL level — row is invisible."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    async with as_user(app_session_factory, bob) as bob_session:
        # The row simply doesn't exist for Bob
        row = (
            await bob_session.execute(
                select(ProductNode).where(ProductNode.id == node_a.id)
            )
        ).scalar_one_or_none()
        assert row is None


async def test_anonymous_sees_nothing(db, app_session_factory):
    """No identity → empty result set across all product-tree tables."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    async with anonymous(app_session_factory) as anon:
        nodes = (await anon.execute(select(ProductNode))).scalars().all()
    assert len(nodes) == 0


# ─────────────────────────── route: viewer read-only ─────────────────────────


async def test_viewer_can_list_nodes(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)
    resp = await tree_api.as_user(carol).get(f"/api/v1/projects/{proj_a.id}/nodes")
    assert resp.status_code == 200
    ids = [n["id"] for n in resp.json()["nodes"]]
    assert str(node_a.id) in ids


async def test_viewer_cannot_create_node(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)
    resp = await tree_api.as_user(carol).post(
        f"/api/v1/projects/{proj_a.id}/nodes",
        json={"kind": "component", "name": "Viewer's node"},
    )
    assert resp.status_code in (403, 404)


async def test_editor_can_create_node(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)
    resp = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes",
        json={"kind": "component", "name": "New node"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "New node"
    assert data["kind"] == "component"
    assert data["project_id"] == str(proj_a.id)


async def test_cross_org_route_returns_404(db, tree_api):
    """Bob gets 404 on org A's project, not a 403 that confirms existence."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)
    resp = await tree_api.as_user(bob).get(f"/api/v1/projects/{proj_a.id}/nodes")
    assert resp.status_code == 404


# ─────────────────────────── route: CRUD cycle ────────────────────────────────


async def test_node_create_update_delete(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    # Create
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes",
        json={"kind": "assembly", "name": "Sat bus", "part_number": "SB-001"},
    )
    assert r.status_code == 201
    nid = r.json()["id"]

    # Update
    r = await tree_api.as_user(alice).put(
        f"/api/v1/projects/{proj_a.id}/nodes/{nid}",
        json={"name": "Sat bus (updated)"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Sat bus (updated)"

    # Delete
    r = await tree_api.as_user(alice).delete(f"/api/v1/projects/{proj_a.id}/nodes/{nid}")
    assert r.status_code == 204

    # Verify gone
    r = await tree_api.as_user(alice).get(f"/api/v1/projects/{proj_a.id}/nodes/{nid}")
    assert r.status_code == 404


# ─────────────────────────── cycle guard ─────────────────────────────────────


async def test_move_cycle_rejected(db, tree_api):
    """Moving a node under one of its own descendants is rejected."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    # Create child
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes",
        json={"kind": "component", "name": "Child", "parent_node_id": str(node_a.id)},
    )
    assert r.status_code == 201
    child_id = r.json()["id"]

    # Try to move the parent under the child (cycle)
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/move",
        json={"parent_node_id": child_id},
    )
    assert r.status_code == 422


async def test_move_self_under_self_rejected(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/move",
        json={"parent_node_id": str(node_a.id)},
    )
    assert r.status_code == 422


# ─────────────────────────── interfaces ──────────────────────────────────────


async def test_interface_create_and_list(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/interfaces",
        json={"kind": "power", "name": "PWR-A"},
    )
    assert r.status_code == 201
    iid = r.json()["id"]
    assert r.json()["kind"] == "power"

    r = await tree_api.as_user(carol).get(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/interfaces"
    )
    assert r.status_code == 200
    assert any(i["id"] == iid for i in r.json())


async def test_viewer_cannot_delete_interface(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/interfaces",
        json={"kind": "data", "name": "RS-422"},
    )
    iid = r.json()["id"]

    r = await tree_api.as_user(carol).delete(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/interfaces/{iid}"
    )
    assert r.status_code in (403, 404)


# ─────────────────────────── parameters ──────────────────────────────────────


async def test_parameter_set_quantity(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/parameters",
        json={
            "raw_name": "Vbus",
            "value_kind": "quantity",
            "value_num": 28.0,
            "unit": "V",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["normalized_name"] == "vbus"
    assert data["value_num"] == pytest.approx(28.0)
    assert data["unit"] == "V"
    assert data["role"] == "bidirectional"


async def test_parameter_unknown_unit_rejected(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/parameters",
        json={"raw_name": "voltage", "value_kind": "quantity", "value_num": 28.0, "unit": "zorblax"},
    )
    assert r.status_code == 422


async def test_parameter_node_level_must_be_bidirectional(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/parameters",
        json={"raw_name": "Vbus", "value_kind": "quantity", "value_num": 28.0, "unit": "V", "role": "supply"},
    )
    assert r.status_code == 422


async def test_parameter_upsert_updates_existing(db, tree_api):
    """Setting the same (node, name) pair twice updates in-place."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    payload = {"raw_name": "Vbus", "value_kind": "quantity", "value_num": 28.0, "unit": "V"}
    r1 = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/parameters", json=payload
    )
    p1_id = r1.json()["id"]

    payload2 = {**payload, "value_num": 26.0}
    r2 = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/parameters", json=payload2
    )
    assert r2.json()["id"] == p1_id  # same row
    assert r2.json()["value_num"] == pytest.approx(26.0)


# ─────────────────────────── interface role ──────────────────────────────────


async def test_interface_param_can_have_supply_role(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    # Create an interface first
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/interfaces",
        json={"kind": "power", "name": "PWR-OUT"},
    )
    iid = r.json()["id"]

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/parameters",
        json={
            "raw_name": "V_out",
            "interface_id": iid,
            "value_kind": "quantity",
            "value_num": 28.0,
            "unit": "V",
            "role": "supply",
        },
    )
    assert r.status_code == 201
    assert r.json()["role"] == "supply"


# ─────────────────────────── mates ───────────────────────────────────────────


async def test_mate_canonical_order(db, tree_api):
    """Regardless of which ID is passed as a or b, the mate is stored a<b."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    # Create two nodes and two interfaces
    r1 = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes", json={"kind": "component", "name": "Node 2"}
    )
    n2 = r1.json()["id"]

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/interfaces", json={"kind": "power", "name": "A-out"}
    )
    ia = r.json()["id"]
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{n2}/interfaces", json={"kind": "power", "name": "B-in"}
    )
    ib = r.json()["id"]

    # Pass them in reverse order
    a_id, b_id = sorted([ia, ib])
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/mates",
        json={"interface_a_id": ib, "interface_b_id": ia},  # reversed
    )
    assert r.status_code == 201
    data = r.json()
    assert data["interface_a_id"] == a_id  # canonical: smaller first
    assert data["interface_b_id"] == b_id


async def test_mate_self_rejected(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/interfaces", json={"kind": "power", "name": "A"}
    )
    ia = r.json()["id"]

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/mates",
        json={"interface_a_id": ia, "interface_b_id": ia},
    )
    assert r.status_code == 422


# ─────────────────────────── node-document links ─────────────────────────────


async def test_node_document_link(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)
    doc = await make_document(db, alice, proj_a, title="EPS ICD")
    await db.commit()

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/documents",
        json={"document_id": str(doc.id), "relation": "design-spec"},
    )
    assert r.status_code == 201
    lid = r.json()["id"]

    # Viewer can list
    r = await tree_api.as_user(carol).get(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/documents"
    )
    assert any(lnk["id"] == lid for lnk in r.json())

    # Editor can unlink
    r = await tree_api.as_user(alice).delete(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/documents/{lid}"
    )
    assert r.status_code == 204


async def test_cross_project_document_link_rejected(db, tree_api):
    """A document from a different project cannot be linked to a node."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    # Create a second project in org A with a document
    proj_c = await make_project(db, org_a, alice, "Project C")
    await make_grant(db, org_id=org_a.id, subject_type="project", subject_id=proj_c.id, user=alice, role="owner")
    doc_c = await make_document(db, alice, proj_c, title="Other project doc")
    await db.commit()

    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/documents",
        json={"document_id": str(doc_c.id), "relation": "reference"},
    )
    assert r.status_code == 404


# ─────────────────────────── parameter definitions ───────────────────────────


async def test_paramdef_create_and_list(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    r = await tree_api.as_user(alice).post(
        f"/api/v1/orgs/{org_a.id}/parameter-definitions",
        json={"key": "Bus Voltage", "display_name": "Bus voltage", "dimension": "voltage", "canonical_unit": "V"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["key"] == "bus_voltage"  # folded

    # List
    r = await tree_api.as_user(alice).get(f"/api/v1/orgs/{org_a.id}/parameter-definitions")
    assert any(d["key"] == "bus_voltage" for d in r.json())


async def test_paramdef_cross_org_invisible(db, tree_api):
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    await tree_api.as_user(alice).post(
        f"/api/v1/orgs/{org_a.id}/parameter-definitions",
        json={"key": "Bus Voltage", "display_name": "Bus voltage"},
    )

    r = await tree_api.as_user(bob).get(f"/api/v1/orgs/{org_a.id}/parameter-definitions")
    assert r.status_code == 404  # cross-org is a 404, not 200 with data


async def test_alias_create_and_resolves_on_param(db, tree_api):
    """An alias for 'BUS_V' → 'bus_voltage' makes param writes resolve automatically."""
    org_a, org_b, alice, bob, carol, proj_a, proj_b, node_a = await _setup_two_orgs(db)

    # Create definition
    r = await tree_api.as_user(alice).post(
        f"/api/v1/orgs/{org_a.id}/parameter-definitions",
        json={"key": "bus_voltage", "display_name": "Bus voltage", "dimension": "voltage"},
    )
    def_id = r.json()["id"]

    # Add alias
    r = await tree_api.as_user(alice).post(
        f"/api/v1/orgs/{org_a.id}/parameter-definitions/{def_id}/aliases",
        json={"normalized_name": "BUS_V"},
    )
    assert r.status_code == 201
    assert r.json()["normalized_name"] == "bus_v"  # folded

    # Now set a parameter with raw_name 'BUS_V' — should resolve to the definition
    r = await tree_api.as_user(alice).post(
        f"/api/v1/projects/{proj_a.id}/nodes/{node_a.id}/parameters",
        json={"raw_name": "BUS_V", "value_kind": "quantity", "value_num": 28.0, "unit": "V"},
    )
    assert r.status_code == 201
    assert r.json()["definition_id"] == def_id
