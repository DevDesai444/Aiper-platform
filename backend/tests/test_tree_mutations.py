"""Project rename, folder rename/move/delete, document move, and id8 resolution.

Same conventions as test_routes.py and test_document_comments.py: real
Postgres, real routers, an in-process `api` fixture that impersonates a user.
Everything above the RLS section runs against the superuser-bypass `api`
fixture, which would pass even if migration 0007's grants were missing
entirely — the RLS section at the bottom, over `rls_api` (the unprivileged
`aiper_app` role migrations 0003/0004 protect), is what actually proves those
grants exist.
"""

from __future__ import annotations

from app.services.permissions import grant_access

from tests.factories import make_document, make_folder, make_org, make_project, make_user

# ─────────────────────────────── project rename ───────────────────────────────


async def test_editor_can_rename_a_project(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=bob.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(bob).patch(
        f"/api/v1/projects/{project.id}", json={"name": "Renamed"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed"


async def test_viewer_cannot_rename_a_project(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=bob.id, role="viewer",
    )
    await db.commit()

    response = await api.as_user(bob).patch(
        f"/api/v1/projects/{project.id}", json={"name": "Renamed"}
    )
    assert response.status_code == 403


async def test_cross_org_rename_project_is_404(api, db):
    org = await make_org(db, "org")
    other = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other, "mallory@other.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(mallory).patch(
        f"/api/v1/projects/{project.id}", json={"name": "Renamed"}
    )
    assert response.status_code == 404


# ──────────────────────────────── folder rename ────────────────────────────────


async def test_editor_can_rename_a_folder(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project, "Old name")
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=folder.id,
        user_id=alice.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/projects/{project.id}/folders/{folder.id}", json={"name": "New name"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "New name"


async def test_cross_org_rename_folder_is_404(api, db):
    org = await make_org(db, "org")
    other = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other, "mallory@other.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project)
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=folder.id,
        user_id=alice.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(mallory).patch(
        f"/api/v1/projects/{project.id}/folders/{folder.id}", json={"name": "x"}
    )
    assert response.status_code == 404


# ───────────────────────────────── folder move ─────────────────────────────────


async def test_editor_can_move_a_folder_into_another_folder(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    source_parent = await make_folder(db, project, "A")
    destination = await make_folder(db, project, "B")
    moving = await make_folder(db, project, "Moving", parent=source_parent)
    for f in (source_parent, destination, moving):
        await grant_access(
            db, org_id=org.id, subject_type="folder", subject_id=f.id,
            user_id=alice.id, role="editor",
        )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/projects/{project.id}/folders/{moving.id}/move",
        json={"parent_folder_id": str(destination.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["parent_folder_id"] == str(destination.id)


async def test_a_folder_can_move_to_the_project_root(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    parent = await make_folder(db, project, "Parent")
    moving = await make_folder(db, project, "Moving", parent=parent)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=moving.id,
        user_id=alice.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/projects/{project.id}/folders/{moving.id}/move",
        json={"parent_folder_id": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["parent_folder_id"] is None


async def test_moving_a_folder_into_itself_is_refused(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project)
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=folder.id,
        user_id=alice.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/projects/{project.id}/folders/{folder.id}/move",
        json={"parent_folder_id": str(folder.id)},
    )
    assert response.status_code == 400


async def test_moving_a_folder_into_its_own_descendant_is_refused(api, db):
    """Grandparent into grandchild: the classic cycle a naive check would miss."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    grandparent = await make_folder(db, project, "Grandparent")
    parent = await make_folder(db, project, "Parent", parent=grandparent)
    child = await make_folder(db, project, "Child", parent=parent)
    for f in (grandparent, parent, child):
        await grant_access(
            db, org_id=org.id, subject_type="folder", subject_id=f.id,
            user_id=alice.id, role="editor",
        )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/projects/{project.id}/folders/{grandparent.id}/move",
        json={"parent_folder_id": str(child.id)},
    )
    assert response.status_code == 400

    # The attempt must not have partially applied.
    unchanged = await api.as_user(alice).get(f"/api/v1/projects/{project.id}/tree")
    by_id = {f["id"]: f for f in unchanged.json()["folders"]}
    assert by_id[str(grandparent.id)]["parent_folder_id"] is None


async def test_moving_a_folder_requires_editor_access_on_the_destination(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, alice)
    moving = await make_folder(db, project, "Moving")
    destination = await make_folder(db, project, "No access for bob")
    # Bob may edit the folder he is moving, but was never granted the
    # destination — the trigger's own invariant, checked here in Python first.
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=moving.id,
        user_id=bob.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(bob).patch(
        f"/api/v1/projects/{project.id}/folders/{moving.id}/move",
        json={"parent_folder_id": str(destination.id)},
    )
    assert response.status_code == 404  # bob cannot see the destination at all


async def test_moving_a_folder_to_a_folder_in_another_project_is_404(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project_a = await make_project(db, org, alice, "A")
    project_b = await make_project(db, org, alice, "B")
    moving = await make_folder(db, project_a, "Moving")
    foreign = await make_folder(db, project_b, "Elsewhere")
    for subject_type, subject_id in (("folder", moving.id), ("folder", foreign.id)):
        await grant_access(
            db, org_id=org.id, subject_type=subject_type, subject_id=subject_id,
            user_id=alice.id, role="owner",
        )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/projects/{project_a.id}/folders/{moving.id}/move",
        json={"parent_folder_id": str(foreign.id)},
    )
    assert response.status_code == 404


async def test_cross_org_move_folder_is_404(api, db):
    org = await make_org(db, "org")
    other = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other, "mallory@other.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project)
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=folder.id,
        user_id=alice.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(mallory).patch(
        f"/api/v1/projects/{project.id}/folders/{folder.id}/move",
        json={"parent_folder_id": None},
    )
    assert response.status_code == 404


# ──────────────────────────────── folder delete ────────────────────────────────


async def test_owner_deletes_a_folder_its_subtree_and_surfaces_documents(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    top = await make_folder(db, project, "Top")
    child = await make_folder(db, project, "Child", parent=top)
    doc_in_top = await make_document(db, alice, project, top, "In top")
    doc_in_child = await make_document(db, alice, project, child, "In child")
    for subject_type, subject_id in (
        ("folder", top.id), ("document", doc_in_top.id), ("document", doc_in_child.id),
    ):
        await grant_access(
            db, org_id=org.id, subject_type=subject_type, subject_id=subject_id,
            user_id=alice.id, role="owner",
        )
    await db.commit()

    response = await api.as_user(alice).delete(f"/api/v1/projects/{project.id}/folders/{top.id}")
    assert response.status_code == 204

    tree = (await api.as_user(alice).get(f"/api/v1/projects/{project.id}/tree")).json()
    assert tree["folders"] == []
    # The documents survive at the project root — not destroyed with the folder.
    remaining = {d["id"]: d for d in tree["documents"]}
    assert remaining[str(doc_in_top.id)]["folder_id"] is None
    assert remaining[str(doc_in_child.id)]["folder_id"] is None


async def test_editor_cannot_delete_a_folder(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project)
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=folder.id,
        user_id=alice.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(alice).delete(f"/api/v1/projects/{project.id}/folders/{folder.id}")
    assert response.status_code == 403


async def test_cross_org_delete_folder_is_404(api, db):
    org = await make_org(db, "org")
    other = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other, "mallory@other.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project)
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=folder.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(mallory).delete(f"/api/v1/projects/{project.id}/folders/{folder.id}")
    assert response.status_code == 404


# ───────────────────────────────── document move ───────────────────────────────


async def test_editor_can_move_a_document_between_folders(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    source = await make_folder(db, project, "Source")
    destination = await make_folder(db, project, "Destination")
    document = await make_document(db, alice, project, source)
    for subject_type, subject_id in (
        ("document", document.id), ("folder", destination.id),
    ):
        await grant_access(
            db, org_id=org.id, subject_type=subject_type, subject_id=subject_id,
            user_id=alice.id, role="editor",
        )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/documents/{document.id}/move", json={"folder_id": str(destination.id)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["folder_id"] == str(destination.id)


async def test_a_document_can_move_to_the_project_root(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project)
    document = await make_document(db, alice, project, folder)
    await grant_access(
        db, org_id=org.id, subject_type="document", subject_id=document.id,
        user_id=alice.id, role="owner",
    )
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/documents/{document.id}/move", json={"folder_id": None}
    )
    assert response.status_code == 200, response.text
    assert response.json()["folder_id"] is None


async def test_moving_a_document_requires_editor_access_on_the_destination(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, alice)
    destination = await make_folder(db, project, "No access for bob")
    document = await make_document(db, alice, project)
    await grant_access(
        db, org_id=org.id, subject_type="document", subject_id=document.id,
        user_id=bob.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(bob).patch(
        f"/api/v1/documents/{document.id}/move", json={"folder_id": str(destination.id)}
    )
    assert response.status_code == 404


async def test_moving_a_document_to_a_folder_in_another_project_is_404(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project_a = await make_project(db, org, alice, "A")
    project_b = await make_project(db, org, alice, "B")
    document = await make_document(db, alice, project_a)
    foreign = await make_folder(db, project_b, "Elsewhere")
    for subject_type, subject_id in (
        ("document", document.id), ("folder", foreign.id),
    ):
        await grant_access(
            db, org_id=org.id, subject_type=subject_type, subject_id=subject_id,
            user_id=alice.id, role="owner",
        )
    await db.commit()

    response = await api.as_user(alice).patch(
        f"/api/v1/documents/{document.id}/move", json={"folder_id": str(foreign.id)}
    )
    assert response.status_code == 404


async def test_cross_org_move_document_is_404(api, db):
    org = await make_org(db, "org")
    other = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other, "mallory@other.example")
    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)
    await grant_access(
        db, org_id=org.id, subject_type="document", subject_id=document.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(mallory).patch(
        f"/api/v1/documents/{document.id}/move", json={"folder_id": None}
    )
    assert response.status_code == 404


# ──────────────────────────────── project delete ───────────────────────────────


async def test_deleting_a_project_with_folders_in_it_succeeds(api, db):
    """Regression test.

    Found by hand, not by this suite: Project.folders had no cascade config,
    so deleting a project made the ORM try to null out folders.project_id
    first (its default one-to-many delete behaviour) — folders.project_id is
    NOT NULL, and the relocation guard trigger (migration 0003) refuses that
    UPDATE regardless, since it looks exactly like a relocation from where the
    trigger sits. A project with no folders never exercised this path, which
    is exactly why the earlier tests here missed it. The fix is
    passive_deletes=True on Project.folders, matching Folder.children.
    """
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    folder = await make_folder(db, project, "Requirements")
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await grant_access(
        db, org_id=org.id, subject_type="folder", subject_id=folder.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).delete(f"/api/v1/projects/{project.id}")
    assert response.status_code == 204, response.text


async def test_deleting_a_project_with_nested_folders_succeeds(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    top = await make_folder(db, project, "Top")
    await make_folder(db, project, "Child", parent=top)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).delete(f"/api/v1/projects/{project.id}")
    assert response.status_code == 204, response.text


# ─────────────────────────────── id8 resolution ────────────────────────────────


async def test_a_project_resolves_by_its_id8_prefix(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    id8 = str(project.id)[:8]
    by_id8 = await api.as_user(alice).get(f"/api/v1/projects/{id8}/tree")
    by_uuid = await api.as_user(alice).get(f"/api/v1/projects/{project.id}/tree")
    assert by_id8.status_code == 200
    assert by_id8.json() == by_uuid.json()


async def test_a_document_resolves_by_its_id8_prefix(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)
    await grant_access(
        db, org_id=org.id, subject_type="document", subject_id=document.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    id8 = str(document.id)[:8]
    response = await api.as_user(alice).get(f"/api/v1/documents/{id8}")
    assert response.status_code == 200
    assert response.json()["id"] == str(document.id)


async def test_id8_resolution_is_scoped_to_the_caller_s_organisation(api, db):
    """A prefix that exists in another organisation is not a match here."""
    org = await make_org(db, "org")
    other = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, other, "mallory@other.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    id8 = str(project.id)[:8]
    response = await api.as_user(mallory).get(f"/api/v1/projects/{id8}/tree")
    assert response.status_code == 404


async def test_an_unmatched_or_malformed_ref_is_404(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    await db.commit()

    for ref in ("00000000", "not-hex!!", "1234567"):  # no match, invalid, too short
        response = await api.as_user(alice).get(f"/api/v1/projects/{ref}/tree")
        assert response.status_code == 404, ref


# `/tree` was the first route to resolve a ref; every other route that takes a
# project id in the URL or a request body was widened to match once a folder
# delete through a slugged URL (id8 in the path, not the full uuid) came back
# a raw pydantic uuid-parsing error instead of a clean result. The fix was
# resolve_ref everywhere a project id appears, not just on the read path — the
# tests below are what that regression looks like, pinned so it cannot recur.


async def test_every_project_id_taking_route_accepts_an_id8_ref(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()
    id8 = str(project.id)[:8]

    renamed = await api.as_user(alice).patch(
        f"/api/v1/projects/{id8}", json={"name": "Renamed via id8"}
    )
    assert renamed.status_code == 200, renamed.text

    folder = await api.as_user(alice).post(
        f"/api/v1/projects/{id8}/folders", json={"name": "Via id8"}
    )
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["id"]

    folder_renamed = await api.as_user(alice).patch(
        f"/api/v1/projects/{id8}/folders/{folder_id}", json={"name": "Renamed"}
    )
    assert folder_renamed.status_code == 200, folder_renamed.text

    moved = await api.as_user(alice).patch(
        f"/api/v1/projects/{id8}/folders/{folder_id}/move", json={"parent_folder_id": None}
    )
    assert moved.status_code == 200, moved.text

    document = await api.as_user(alice).post(
        "/api/v1/documents", json={"title": "Doc", "project_id": id8}
    )
    assert document.status_code == 201, document.text

    deleted = await api.as_user(alice).delete(f"/api/v1/projects/{id8}/folders/{folder_id}")
    assert deleted.status_code == 204, deleted.text

    deleted_project = await api.as_user(alice).delete(f"/api/v1/projects/{id8}")
    assert deleted_project.status_code == 204, deleted_project.text


async def test_create_from_markdown_accepts_an_id8_project_ref(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).post(
        "/api/v1/documents/from-markdown",
        json={"title": "Draft", "markdown": "# Draft", "project_id": str(project.id)[:8]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["project_id"] == str(project.id)


# ───────────────────────── proof under row-level security ─────────────────────

# Everything above runs against the superuser-bypass `api` fixture, which would
# pass even if migration 0007's grants were missing entirely — RLS exempts a
# BYPASSRLS/superuser role outright, and the coarse GRANT layer sits *beneath*
# the row policies, invisible to a role that skips row security altogether.
# This is the one test that actually proves the grants exist: the same routes,
# over a session connected as the unprivileged `aiper_app` role, with
# migration 0003's policies and 0007's grants both live underneath.


async def test_the_new_mutations_work_as_the_app_role(db, rls_api):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    await db.commit()

    created = await rls_api.as_user(alice).post("/api/v1/projects", json={"name": "Mission"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    renamed = await rls_api.as_user(alice).patch(
        f"/api/v1/projects/{project_id}", json={"name": "Mission Renamed"}
    )
    assert renamed.status_code == 200, renamed.text

    folder = await rls_api.as_user(alice).post(
        f"/api/v1/projects/{project_id}/folders", json={"name": "Requirements"}
    )
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["id"]

    folder_renamed = await rls_api.as_user(alice).patch(
        f"/api/v1/projects/{project_id}/folders/{folder_id}", json={"name": "Renamed folder"}
    )
    assert folder_renamed.status_code == 200, folder_renamed.text

    second_folder = await rls_api.as_user(alice).post(
        f"/api/v1/projects/{project_id}/folders", json={"name": "Baseline"}
    )
    assert second_folder.status_code == 201
    second_folder_id = second_folder.json()["id"]

    moved_folder = await rls_api.as_user(alice).patch(
        f"/api/v1/projects/{project_id}/folders/{folder_id}/move",
        json={"parent_folder_id": second_folder_id},
    )
    assert moved_folder.status_code == 200, moved_folder.text

    document = await rls_api.as_user(alice).post(
        "/api/v1/documents", json={"title": "SRS", "project_id": project_id}
    )
    assert document.status_code == 201
    document_id = document.json()["id"]

    moved_document = await rls_api.as_user(alice).patch(
        f"/api/v1/documents/{document_id}/move", json={"folder_id": folder_id}
    )
    assert moved_document.status_code == 200, moved_document.text

    deleted = await rls_api.as_user(alice).delete(
        f"/api/v1/projects/{project_id}/folders/{second_folder_id}"
    )
    assert deleted.status_code == 204, deleted.text

    # Deleting second_folder cascade-deletes its child (the renamed folder,
    # moved under it above), which in turn SET NULLs the document that was
    # inside that child — surfacing it at the project root rather than
    # destroying it. Proven end to end as the app role, not assumed from the
    # superuser suite.
    tree = await rls_api.as_user(alice).get(f"/api/v1/projects/{project_id}/tree")
    assert tree.status_code == 200
    docs = {d["id"]: d for d in tree.json()["documents"]}
    assert docs[document_id]["folder_id"] is None
