"""The routes' side of the contract: what the resolver's answer becomes on the wire.

The distinction that matters here is 404 against 403. A subject the caller
cannot reach must look exactly like one that does not exist, because "403
Forbidden" on an unreachable document confirms that it exists — and in a shared
database that leaks the contents of another organisation.
"""

from __future__ import annotations

import uuid

from app.services.permissions import grant_access

from tests.factories import make_document, make_folder, make_org, make_project, make_user


async def test_creating_a_project_makes_the_creator_its_owner(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    await db.commit()

    response = await api.as_user(alice).post(
        "/api/v1/projects", json={"name": "Launch campaign", "description": "Q3"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Launch campaign"
    assert body["access"] == "owner"


async def test_listing_projects_shows_only_what_the_caller_can_reach(api, db):
    org = await make_org(db, "org")
    other_org = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    mallory = await make_user(db, other_org, "mallory@other.example")

    shared = await make_project(db, org, alice, "shared")
    private = await make_project(db, org, alice, "private")
    for project in (shared, private):
        await grant_access(
            db,
            org_id=org.id,
            subject_type="project",
            subject_id=project.id,
            user_id=alice.id,
            role="owner",
        )
    await grant_access(
        db,
        org_id=org.id,
        subject_type="project",
        subject_id=shared.id,
        user_id=bob.id,
        role="viewer",
    )
    await db.commit()

    alice_sees = {p["name"]: p["access"] for p in (await api.as_user(alice).get("/api/v1/projects")).json()}
    assert alice_sees == {"shared": "owner", "private": "owner"}

    bob_sees = {p["name"]: p["access"] for p in (await api.as_user(bob).get("/api/v1/projects")).json()}
    assert bob_sees == {"shared": "viewer"}

    assert (await api.as_user(mallory).get("/api/v1/projects")).json() == []


async def test_an_unreachable_document_is_reported_as_missing(api, db):
    """No access at all: 404, identical to a document that was never created."""
    org = await make_org(db, "org")
    other_org = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other_org, "mallory@other.example")

    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)
    await db.commit()

    real = await api.as_user(mallory).get(f"/api/v1/documents/{document.id}")
    invented = await api.as_user(mallory).get(f"/api/v1/documents/{uuid.uuid4()}")

    assert real.status_code == 404
    assert invented.status_code == 404
    assert real.json() == invented.json(), "the two must be indistinguishable"


async def test_a_viewer_may_read_but_not_write(api, db):
    """Access established, role too low: 403, not 404."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)
    await grant_access(
        db,
        org_id=org.id,
        subject_type="document",
        subject_id=document.id,
        user_id=bob.id,
        role="viewer",
    )
    await db.commit()

    assert (await api.as_user(bob).get(f"/api/v1/documents/{document.id}")).status_code == 200
    rename = await api.as_user(bob).patch(
        f"/api/v1/documents/{document.id}", json={"title": "Renamed"}
    )
    assert rename.status_code == 403
    delete = await api.as_user(bob).delete(f"/api/v1/documents/{document.id}")
    assert delete.status_code == 403


async def test_an_editor_may_write_but_not_delete_or_share(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)
    await grant_access(
        db,
        org_id=org.id,
        subject_type="document",
        subject_id=document.id,
        user_id=bob.id,
        role="editor",
    )
    await db.commit()

    rename = await api.as_user(bob).patch(
        f"/api/v1/documents/{document.id}", json={"title": "Renamed"}
    )
    assert rename.status_code == 200

    assert (await api.as_user(bob).delete(f"/api/v1/documents/{document.id}")).status_code == 403
    share = await api.as_user(bob).post(
        f"/api/v1/documents/{document.id}/collaborators",
        json={"email": "carol@org.example", "role": "viewer"},
    )
    assert share.status_code == 403


async def test_creating_a_document_requires_write_access_to_its_destination(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    reader = await make_user(db, org, "reader@org.example")

    project = await make_project(db, org, alice)
    await grant_access(
        db,
        org_id=org.id,
        subject_type="project",
        subject_id=project.id,
        user_id=alice.id,
        role="owner",
    )
    await grant_access(
        db,
        org_id=org.id,
        subject_type="project",
        subject_id=project.id,
        user_id=reader.id,
        role="viewer",
    )
    await db.commit()

    created = await api.as_user(alice).post(
        "/api/v1/documents", json={"title": "Plan", "project_id": str(project.id)}
    )
    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == str(project.id)

    # A viewer on the project cannot add to it.
    refused = await api.as_user(reader).post(
        "/api/v1/documents", json={"title": "Nope", "project_id": str(project.id)}
    )
    assert refused.status_code == 403

    # Someone with no access at all must not even learn the project exists.
    unknown = await api.as_user(bob).post(
        "/api/v1/documents", json={"title": "Nope", "project_id": str(project.id)}
    )
    assert unknown.status_code == 404


async def test_the_tree_shows_only_the_caller_s_own_subtree(api, db):
    """A folder grantee sees their folder and its documents, and no project header."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, alice)
    await grant_access(
        db,
        org_id=org.id,
        subject_type="project",
        subject_id=project.id,
        user_id=alice.id,
        role="owner",
    )
    shared = await make_folder(db, project, "shared")
    secret = await make_folder(db, project, "secret")
    inside = await make_document(db, alice, project, shared, "inside shared")
    hidden = await make_document(db, alice, project, secret, "inside secret")

    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=shared.id,
        user_id=bob.id,
        role="viewer",
    )
    await db.commit()

    full = (await api.as_user(alice).get(f"/api/v1/projects/{project.id}/tree")).json()
    assert full["project"]["access"] == "owner"
    assert {f["name"] for f in full["folders"]} == {"shared", "secret"}
    assert {d["title"] for d in full["documents"]} == {"inside shared", "inside secret"}

    partial = (await api.as_user(bob).get(f"/api/v1/projects/{project.id}/tree")).json()
    assert partial["project"] is None, "bob cannot see the project itself"
    assert {f["name"] for f in partial["folders"]} == {"shared"}
    assert {d["title"] for d in partial["documents"]} == {"inside shared"}
    assert str(hidden.id) not in str(partial)
    assert str(inside.id) in str(partial)


async def test_a_tree_the_caller_cannot_see_is_reported_as_missing(api, db):
    org = await make_org(db, "org")
    other_org = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other_org, "mallory@other.example")

    project = await make_project(db, org, alice)
    await make_document(db, alice, project)
    await db.commit()

    response = await api.as_user(mallory).get(f"/api/v1/projects/{project.id}/tree")
    assert response.status_code == 404


async def test_a_malformed_id_looks_like_a_missing_subject(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    await db.commit()

    response = await api.as_user(alice).post(
        "/api/v1/projects/not-a-uuid/folders", json={"name": "F"}
    )
    assert response.status_code == 404


async def test_sharing_a_document_grants_access_through_the_resolver(api, db):
    """The legacy share route writes a grant; the resolver is what answers."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    carol = await make_user(db, org, "carol@org.example")

    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)
    await grant_access(
        db,
        org_id=org.id,
        subject_type="document",
        subject_id=document.id,
        user_id=alice.id,
        role="owner",
    )
    await db.commit()

    assert (await api.as_user(carol).get(f"/api/v1/documents/{document.id}")).status_code == 404

    shared = await api.as_user(alice).post(
        f"/api/v1/documents/{document.id}/collaborators",
        json={"email": "carol@org.example", "role": "viewer"},
    )
    assert shared.status_code == 201, shared.text
    assert (await api.as_user(carol).get(f"/api/v1/documents/{document.id}")).status_code == 200

    removed = await api.as_user(alice).delete(
        f"/api/v1/documents/{document.id}/collaborators/{shared.json()['id']}"
    )
    assert removed.status_code == 204
    assert (await api.as_user(carol).get(f"/api/v1/documents/{document.id}")).status_code == 404


async def test_the_agent_path_falls_back_to_the_caller_s_workspace(api, db):
    """from-markdown may omit project_id: a client that predates projects still works."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    await db.commit()

    response = await api.as_user(alice).post(
        "/api/v1/documents/from-markdown",
        json={"title": "Generated", "markdown": "# Heading\n\nBody."},
    )
    assert response.status_code == 201, response.text
    project_id = response.json()["project_id"]
    assert project_id is not None

    tree = (await api.as_user(alice).get(f"/api/v1/projects/{project_id}/tree")).json()
    assert tree["project"]["name"] == "Workspace"
    assert tree["project"]["access"] == "owner"
    assert [d["title"] for d in tree["documents"]] == ["Generated"]

    # A second call reuses the same workspace rather than founding another.
    again = await api.as_user(alice).post(
        "/api/v1/documents/from-markdown",
        json={"title": "Generated again", "markdown": "More."},
    )
    assert again.json()["project_id"] == project_id
    assert len((await api.as_user(alice).get("/api/v1/projects")).json()) == 1


async def test_folders_nest_and_reject_a_parent_from_another_project(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    mine = await make_project(db, org, alice, "mine")
    theirs = await make_project(db, org, alice, "theirs")
    for project in (mine, theirs):
        await grant_access(
            db,
            org_id=org.id,
            subject_type="project",
            subject_id=project.id,
            user_id=alice.id,
            role="owner",
        )
    elsewhere = await make_folder(db, theirs, "elsewhere")
    await db.commit()

    top = await api.as_user(alice).post(
        f"/api/v1/projects/{mine.id}/folders", json={"name": "top"}
    )
    assert top.status_code == 201, top.text
    assert top.json()["parent_folder_id"] is None

    nested = await api.as_user(alice).post(
        f"/api/v1/projects/{mine.id}/folders",
        json={"name": "nested", "parent_folder_id": top.json()["id"]},
    )
    assert nested.status_code == 201
    assert nested.json()["parent_folder_id"] == top.json()["id"]

    # The same-project invariant the schema cannot express.
    foreign = await api.as_user(alice).post(
        f"/api/v1/projects/{mine.id}/folders",
        json={"name": "smuggled", "parent_folder_id": str(elsewhere.id)},
    )
    assert foreign.status_code == 404


async def test_listing_documents_shows_only_what_the_caller_can_reach(api, db):
    org = await make_org(db, "org")
    other_org = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    mallory = await make_user(db, other_org, "mallory@other.example")

    project = await make_project(db, org, alice)
    folder = await make_folder(db, project, "shared")
    in_folder = await make_document(db, alice, project, folder, "in folder")
    await make_document(db, alice, project, None, "at root")

    for document_id in (in_folder.id,):
        await grant_access(
            db,
            org_id=org.id,
            subject_type="document",
            subject_id=document_id,
            user_id=alice.id,
            role="owner",
        )
    # Bob reaches the document only through the folder above it.
    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=folder.id,
        user_id=bob.id,
        role="editor",
    )
    await db.commit()

    bob_sees = {d["title"]: d["access"] for d in (await api.as_user(bob).get("/api/v1/documents")).json()}
    assert bob_sees == {"in folder": "editor"}

    assert (await api.as_user(mallory).get("/api/v1/documents")).json() == []
