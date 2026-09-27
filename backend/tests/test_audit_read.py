"""GET /api/v1/audit/{documents,projects}/{ref}: the read path for "Activity".

aiper_audit_read is a SECURITY DEFINER function gated on aiper_effective_access,
same shape as the verifiers in test_audit_chain.py — and like their own HTTP-
facing tests, these run over `rls_api`, not `api`: `api`'s app doesn't even
mount the audit router (see conftest.py), and more importantly, only the
unprivileged `aiper_app` role actually proves migration 0008's GRANT EXECUTE
is correct — a superuser-bypass connection would pass even if it were missing.

Both gates have to agree: viewer sees events, no-access sees 404, same
anti-enumeration posture as the rest of the app.
"""

from __future__ import annotations

from app.services.permissions import grant_access

from tests.factories import make_document, make_org, make_project, make_user


async def test_document_audit_log_lists_events_for_a_viewer(rls_api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    created = (
        await rls_api.as_user(alice).post(
            "/api/v1/documents",
            json={"title": "Spec", "project_id": str(project.id)},
        )
    ).json()
    document_id = created["id"]

    await rls_api.as_user(alice).patch(
        f"/api/v1/documents/{document_id}", json={"title": "Spec v2"}
    )

    await grant_access(
        db, org_id=org.id, subject_type="document", subject_id=document_id,
        user_id=bob.id, role="viewer",
    )
    await db.commit()

    response = await rls_api.as_user(bob).get(f"/api/v1/audit/documents/{document_id}")
    assert response.status_code == 200, response.text
    entries = response.json()
    actions = [e["action"] for e in entries]
    # Newest first.
    assert actions[0] == "document.rename"
    assert "document.create" in actions
    create_event = next(e for e in entries if e["action"] == "document.create")
    assert create_event["actor_email"] == "alice@org.example"
    assert create_event["actor_name"] == "alice"


async def test_document_audit_log_404s_with_no_access(rls_api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    other_org = await make_org(db, "other")
    mallory = await make_user(db, other_org, "mallory@other.example")
    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)
    await grant_access(
        db, org_id=org.id, subject_type="document", subject_id=document.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await rls_api.as_user(mallory).get(f"/api/v1/audit/documents/{document.id}")
    assert response.status_code == 404


async def test_project_audit_log_lists_events_for_a_viewer(rls_api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    await db.commit()

    # Via the real route, not the factory: only create_project itself calls
    # audit.record_audit for project.create, and it grants alice owner too.
    created = (await rls_api.as_user(alice).post("/api/v1/projects", json={"name": "P"})).json()
    project_id = created["id"]
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project_id,
        user_id=bob.id, role="viewer",
    )
    await db.commit()

    await rls_api.as_user(alice).patch(f"/api/v1/projects/{project_id}", json={"name": "Renamed"})

    response = await rls_api.as_user(bob).get(f"/api/v1/audit/projects/{project_id}")
    assert response.status_code == 200, response.text
    actions = [e["action"] for e in response.json()]
    assert actions[0] == "project.rename"
    assert "project.create" in actions


async def test_project_audit_log_404s_with_no_access(rls_api, db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    other_org = await make_org(db, "other")
    mallory = await make_user(db, other_org, "mallory@other.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    response = await rls_api.as_user(mallory).get(f"/api/v1/audit/projects/{project.id}")
    assert response.status_code == 404


async def test_project_audit_log_accepts_an_id8_ref(rls_api, db):
    """The 'Activity' menu item links through the same id8 URLs as everything else."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    await db.commit()
    created = (await rls_api.as_user(alice).post("/api/v1/projects", json={"name": "P"})).json()

    response = await rls_api.as_user(alice).get(f"/api/v1/audit/projects/{created['id'][:8]}")
    assert response.status_code == 200, response.text
    assert response.json()[0]["action"] == "project.create"


async def test_folder_creation_does_not_leak_into_the_project_audit_log(rls_api, db):
    """Direct-subject events only: a folder's own events live under its own
    subject_id, not its parent project's -- the rollup is a noted follow-up,
    not built here."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    project = await make_project(db, org, alice)
    await grant_access(
        db, org_id=org.id, subject_type="project", subject_id=project.id,
        user_id=alice.id, role="owner",
    )
    await db.commit()

    # A real folder.create event, via the actual route -- not the factory,
    # which inserts the row directly and never touches audit.record_audit.
    await rls_api.as_user(alice).post(
        f"/api/v1/projects/{project.id}/folders", json={"name": "Requirements"}
    )

    response = await rls_api.as_user(alice).get(f"/api/v1/audit/projects/{project.id}")
    assert response.status_code == 200, response.text
    actions = [e["action"] for e in response.json()]
    assert "folder.create" not in actions
