"""POST/GET/DELETE /api/v1/projects/{ref}/members: project-scoped sharing.

Backed directly by access_grants via grant_access/revoke_access -- no
separate pending-invite table the way document sharing has one. A share
target must already be a real user in the caller's own org; anything else is
a clean 404, never an inert grant waiting for someone who may not exist.
"""

from __future__ import annotations

from app.services.permissions import grant_access

from tests.factories import make_org, make_project, make_user


async def test_owner_can_add_a_member_by_email(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).post(
        f"/api/v1/projects/{project.id}/members", json={"email": "bob@org.example", "role": "editor"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "bob@org.example"
    assert body["role"] == "editor"
    assert body["user_id"] == str(bob.id)

    listing = await api.as_user(alice).get(f"/api/v1/projects/{project.id}/members")
    emails = {m["email"] for m in listing.json()}
    assert emails == {"alice@org.example", "bob@org.example"}


async def test_adding_a_cross_org_email_404s_instead_of_granting_nothing(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    other_org = await make_org(db, "other")
    await make_user(db, other_org, "eve@other.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).post(
        f"/api/v1/projects/{project.id}/members", json={"email": "eve@other.example", "role": "viewer"}
    )
    assert response.status_code == 404


async def test_adding_an_unknown_email_404s(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).post(
        f"/api/v1/projects/{project.id}/members", json={"email": "nobody@org.example", "role": "viewer"}
    )
    assert response.status_code == 404


async def test_editor_cannot_add_a_member(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    carol = await make_user(db, org, "carol@org.example")
    await make_user(db, org, "dave@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=carol.id, role="editor",
    )
    await db.commit()

    response = await api.as_user(carol).post(
        f"/api/v1/projects/{project.id}/members", json={"email": "dave@org.example", "role": "viewer"}
    )
    assert response.status_code == 403


async def test_viewer_can_list_but_a_stranger_gets_404(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    carol = await make_user(db, org, "carol@org.example")
    other_org = await make_org(db, "other")
    mallory = await make_user(db, other_org, "mallory@other.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=carol.id, role="viewer",
    )
    await db.commit()

    ok = await api.as_user(carol).get(f"/api/v1/projects/{project.id}/members")
    assert ok.status_code == 200

    blocked = await api.as_user(mallory).get(f"/api/v1/projects/{project.id}/members")
    assert blocked.status_code == 404


async def test_owner_can_remove_a_member(api, db):
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

    response = await api.as_user(alice).delete(f"/api/v1/projects/{project.id}/members/{bob.id}")
    assert response.status_code == 204

    listing = await api.as_user(alice).get(f"/api/v1/projects/{project.id}/members")
    assert {m["email"] for m in listing.json()} == {"alice@org.example"}


async def test_the_last_owner_cannot_be_removed(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).delete(f"/api/v1/projects/{project.id}/members/{alice.id}")
    assert response.status_code == 409


async def test_an_owner_can_be_removed_when_another_owner_remains(api, db):
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
        user_id=bob.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(bob).delete(f"/api/v1/projects/{project.id}/members/{alice.id}")
    assert response.status_code == 204


async def test_member_routes_accept_an_id8_ref(api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await api.as_user(alice).get(f"/api/v1/projects/{str(project.id)[:8]}/members")
    assert response.status_code == 200


# ─────────────────────────────────── RLS proof ──────────────────────────────
#
# The routes above run against the superuser-bypass fixture, which would pass
# even if access_grants had no privileges for aiper_app at all. This proves
# the claim in the brief -- "access_grants already granted to aiper_app,
# confirm, don't assume" -- against the real unprivileged role.


async def test_add_and_remove_a_member_as_the_app_role(rls_api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    added = await rls_api.as_user(alice).post(
        f"/api/v1/projects/{project.id}/members", json={"email": "bob@org.example", "role": "viewer"}
    )
    assert added.status_code == 201, added.text

    removed = await rls_api.as_user(alice).delete(f"/api/v1/projects/{project.id}/members/{bob.id}")
    assert removed.status_code == 204, removed.text
