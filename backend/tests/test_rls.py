"""Row-level security, tested as the role the API actually runs as.

Everything here connects as ``aiper_app`` — no superuser, no BYPASSRLS — so the
policies of migration 0003 are live underneath every statement. The point of
the suite is the property the platform promises: with the application checks
out of the picture entirely (these tests issue raw SQL), a user cannot reach
another tenant's rows, and a session with no authenticated identity cannot
reach anything at all.

Scenario data is arranged through the superuser ``db`` fixture — the policies
under test must not get in the way of building the world they are tested
against.
"""

from __future__ import annotations

import uuid

import pytest
from app.db.models import AccessGrant, Document, DocumentCollaborator, Project, Revision, User
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.conftest import anonymous, as_user
from tests.factories import (
    make_document,
    make_folder,
    make_grant,
    make_org,
    make_project,
    make_user,
)


async def _two_orgs(db):
    """Two tenants, one document: the standing fixture of this suite.

    Alice owns a document in org A (grant included, as the routes would write
    it). Bob is another organisation entirely; Carol shares Alice's
    organisation but holds no grant.
    """
    org_a = await make_org(db, "org-a")
    org_b = await make_org(db, "org-b")
    alice = await make_user(db, org_a, "alice@a.example")
    bob = await make_user(db, org_b, "bob@b.example")
    carol = await make_user(db, org_a, "carol@a.example")

    project = await make_project(db, org_a, alice)
    document = await make_document(db, alice, project, title="Alpha secrets")
    await make_grant(
        db,
        org_id=org_a.id,
        subject_type="project",
        subject_id=project.id,
        user=alice,
        role="owner",
    )
    await make_grant(
        db,
        org_id=org_a.id,
        subject_type="document",
        subject_id=document.id,
        user=alice,
        role="owner",
    )
    await db.commit()
    return org_a, org_b, alice, bob, carol, project, document


# ───────────────────────── the isolation property itself ────────────────────


async def test_cross_org_rows_do_not_exist_at_the_sql_level(db, app_session_factory):
    """User B selecting org A's tables gets zero rows — no WHERE clause needed.

    This is the CTO requirement stated as SQL: the application layer is not
    part of this test, and the query even asks for the other organisation's
    rows explicitly. The database returns none, because for this session they
    do not exist.
    """
    org_a, _, alice, bob, _, project, document = await _two_orgs(db)

    async with as_user(app_session_factory, bob) as session:
        for entity in (Document, Project, Revision, AccessGrant):
            rows = (await session.execute(select(entity))).scalars().all()
            assert rows == [], f"bob can see {entity.__tablename__} rows from another org"
        # Naming the target explicitly changes nothing.
        stolen = (
            await session.execute(select(Document).where(Document.org_id == org_a.id))
        ).scalars().all()
        assert stolen == []
        # Neither does asking for the row by primary key.
        by_id = (
            await session.execute(select(Document).where(Document.id == document.id))
        ).scalar_one_or_none()
        assert by_id is None

    async with as_user(app_session_factory, alice) as session:
        mine = (await session.execute(select(Document))).scalars().all()
        assert [d.id for d in mine] == [document.id]


async def test_same_org_without_a_grant_is_still_nothing(db, app_session_factory):
    """The organisation is the outer boundary, not the access decision."""
    _, _, _, _, carol, _, document = await _two_orgs(db)

    async with as_user(app_session_factory, carol) as session:
        rows = (await session.execute(select(Document))).scalars().all()
        assert rows == []
        by_id = (
            await session.execute(select(Document).where(Document.id == document.id))
        ).scalar_one_or_none()
        assert by_id is None


async def test_an_unbound_session_sees_and_writes_nothing(db, app_session_factory):
    """No identity, no rows: the failure posture of a forgotten set_config."""
    _, _, alice, _, _, project, document = await _two_orgs(db)

    async with anonymous(app_session_factory) as session:
        for entity in (Document, Project, Revision, AccessGrant, User):
            rows = (await session.execute(select(entity))).scalars().all()
            assert rows == [], f"anonymous session can read {entity.__tablename__}"

        with pytest.raises(ProgrammingError, match="row-level security"):
            await session.execute(
                text(
                    "INSERT INTO documents (id, owner_id, org_id, project_id, title,"
                    " content_json, content_text, revision_count, created_at, updated_at)"
                    " VALUES (gen_random_uuid(), :u, :o, :p, 'smuggled', '{}', '', 0, now(), now())"
                ),
                {"u": str(alice.id), "o": str(document.org_id), "p": str(project.id)},
            )


async def test_writes_against_foreign_rows_touch_nothing(db, app_session_factory):
    """UPDATE and DELETE from another tenant match zero rows, silently.

    Silent is correct here: an error would confirm the row exists.
    """
    _, _, _, bob, _, _, document = await _two_orgs(db)

    async with as_user(app_session_factory, bob) as session:
        result = await session.execute(
            update(Document).where(Document.id == document.id).values(title="defaced")
        )
        assert result.rowcount == 0
        result = await session.execute(
            text("DELETE FROM documents WHERE id = :id"), {"id": str(document.id)}
        )
        assert result.rowcount == 0
        await session.commit()

    title = (
        await db.execute(select(Document.title).where(Document.id == document.id))
    ).scalar_one()
    assert title == "Alpha secrets"


async def test_a_viewer_grant_opens_reads_and_nothing_else(db, app_session_factory):
    """Grants translate to SQL ability exactly: viewer reads, editor writes,
    only the owner deletes."""
    org_a, _, alice, _, carol, _, document = await _two_orgs(db)
    await make_grant(
        db,
        org_id=org_a.id,
        subject_type="document",
        subject_id=document.id,
        user=carol,
        role="viewer",
    )
    await db.commit()

    async with as_user(app_session_factory, carol) as session:
        visible = (
            await session.execute(select(Document).where(Document.id == document.id))
        ).scalar_one_or_none()
        assert visible is not None

        # A viewer's UPDATE matches no rows; so does their DELETE.
        result = await session.execute(
            update(Document).where(Document.id == document.id).values(title="defaced")
        )
        assert result.rowcount == 0
        result = await session.execute(
            text("DELETE FROM documents WHERE id = :id"), {"id": str(document.id)}
        )
        assert result.rowcount == 0
        await session.rollback()

    # Editor: writes yes, delete still no.
    await db.execute(
        update(AccessGrant)
        .where(AccessGrant.subject_id == document.id, AccessGrant.user_id == carol.id)
        .values(role="editor")
    )
    await db.commit()
    async with as_user(app_session_factory, carol) as session:
        result = await session.execute(
            update(Document).where(Document.id == document.id).values(title="edited")
        )
        assert result.rowcount == 1
        result = await session.execute(
            text("DELETE FROM documents WHERE id = :id"), {"id": str(document.id)}
        )
        assert result.rowcount == 0
        await session.commit()


async def test_a_forged_cross_org_grant_stays_inert_under_rls(db, app_session_factory):
    """Belt and braces: 0002 proved the resolver ignores such a grant; this
    proves the policies built on the resolver do too."""
    _, org_b, _, bob, _, _, document = await _two_orgs(db)
    await make_grant(
        db,
        org_id=org_b.id,  # the attacker's own org_id, so no column gives it away
        subject_type="document",
        subject_id=document.id,
        user=bob,
        role="owner",
    )
    await db.commit()

    async with as_user(app_session_factory, bob) as session:
        rows = (
            await session.execute(select(Document).where(Document.id == document.id))
        ).scalars().all()
        assert rows == []


async def test_grant_escalation_is_refused_by_with_check(db, app_session_factory):
    """A non-owner cannot write themselves (or anyone) a grant."""
    org_a, _, _, _, carol, _, document = await _two_orgs(db)
    await make_grant(
        db,
        org_id=org_a.id,
        subject_type="document",
        subject_id=document.id,
        user=carol,
        role="viewer",
    )
    await db.commit()

    async with as_user(app_session_factory, carol) as session:
        with pytest.raises(ProgrammingError, match="row-level security"):
            await session.execute(
                text(
                    "INSERT INTO access_grants"
                    " (id, org_id, subject_type, subject_id, user_id, role, granted_by, created_at)"
                    " VALUES (gen_random_uuid(), :org, 'document', :doc, :me, 'owner', :me, now())"
                ),
                {"org": str(org_a.id), "doc": str(document.id), "me": str(carol.id)},
            )


async def test_users_directory_stops_at_the_org_boundary(db, app_session_factory):
    """Alice can see her colleagues; other organisations' accounts do not exist."""
    _, _, alice, bob, carol, _, _ = await _two_orgs(db)

    async with as_user(app_session_factory, alice) as session:
        emails = set((await session.execute(select(User.email))).scalars().all())
    assert emails == {"alice@a.example", "carol@a.example"}
    assert bob.email not in emails


# ─────────────────────── the resolver anchor, closed twice ──────────────────


async def test_a_document_cannot_reference_a_folder_in_another_project(db):
    """The review finding on #3, closed structurally: the composite foreign key
    (folder_id, project_id) → folders (id, project_id) makes the corrupt row
    unwritable — even for the superuser this test connects as."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    hers = await make_project(db, org, alice, "hers")
    other = await make_project(db, org, alice, "other")
    foreign_folder = await make_folder(db, other, "foreign")

    with pytest.raises(IntegrityError):
        await make_document(db, alice, hers, foreign_folder)
    await db.rollback()


async def test_the_resolver_project_checks_its_anchor(db):
    """…and closed behaviourally: even a row forged past the constraint (system
    triggers disabled — a superuser rewriting the database under the
    application) anchors the ancestry walk at nothing, so the foreign folder's
    grant carries nothing in."""
    from app.services.permissions import effective_access

    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    mallory = await make_user(db, org, "mallory@org.example")
    hers = await make_project(db, org, alice, "hers")
    his = await make_project(db, org, mallory, "his")
    his_folder = await make_folder(db, his, "his folder")
    document = await make_document(db, alice, hers)
    await make_grant(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=his_folder.id,
        user=mallory,
        role="owner",
    )
    await db.commit()

    await db.execute(text("ALTER TABLE documents DISABLE TRIGGER ALL"))
    await db.execute(
        text("UPDATE documents SET folder_id = :f WHERE id = :d"),
        {"f": str(his_folder.id), "d": str(document.id)},
    )
    await db.execute(text("ALTER TABLE documents ENABLE TRIGGER ALL"))
    await db.commit()

    assert await effective_access(db, mallory.id, "document", document.id) is None


# ──────────────────────── the routes, policies underneath ───────────────────


async def test_the_full_document_flow_works_as_the_app_role(db, rls_api):
    """Owner, viewer, stranger and another tenant, through the real routes on
    the unprivileged engine: RLS underneath the application checks changes the
    answers for nobody."""
    org_a = await make_org(db, "org-a")
    org_b = await make_org(db, "org-b")
    alice = await make_user(db, org_a, "alice@a.example")
    carol = await make_user(db, org_a, "carol@a.example")
    dave = await make_user(db, org_a, "dave@a.example")
    bob = await make_user(db, org_b, "bob@b.example")
    await db.commit()

    # Alice: project, document, a commit, a restore.
    response = await rls_api.as_user(alice).post("/api/v1/projects", json={"name": "Mission"})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]

    response = await rls_api.as_user(alice).post(
        "/api/v1/documents", json={"title": "SRS", "project_id": project_id}
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]

    response = await rls_api.as_user(alice).post(
        f"/api/v1/documents/{document_id}/commits",
        json={
            "content_json": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "v2"}]}
            ]},
            "commit_message": "second",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision_count"] == 2

    # Share to Carol as viewer: she reads, she cannot commit.
    response = await rls_api.as_user(alice).post(
        f"/api/v1/documents/{document_id}/collaborators",
        json={"email": carol.email, "role": "viewer"},
    )
    assert response.status_code == 201, response.text

    response = await rls_api.as_user(carol).get(f"/api/v1/documents/{document_id}")
    assert response.status_code == 200
    assert response.json()["access"] == "viewer"

    response = await rls_api.as_user(carol).post(
        f"/api/v1/documents/{document_id}/commits",
        json={"content_json": {"type": "doc", "content": []}, "commit_message": "nope"},
    )
    assert response.status_code == 403

    # Dave (same org, no grant) and Bob (another org): the document does not exist.
    for outsider in (dave, bob):
        response = await rls_api.as_user(outsider).get(f"/api/v1/documents/{document_id}")
        assert response.status_code == 404
        listing = await rls_api.as_user(outsider).get("/api/v1/documents")
        assert listing.status_code == 200
        assert listing.json() == []

    # Owner deletes; history goes with it (the one legitimate cascade).
    response = await rls_api.as_user(alice).delete(f"/api/v1/documents/{document_id}")
    assert response.status_code == 204
    remaining = (
        await db.execute(select(func.count()).select_from(Revision))
    ).scalar_one()
    assert remaining == 0


async def test_pending_share_is_claimed_at_registration(
    db, rls_api, app_session_factory, legacy_configured
):
    """A share to an address with no account yet is linked when that account
    registers — through the SECURITY DEFINER claim, since the new user cannot
    see the collaborator row on a document they cannot reach."""
    org = await make_org(db, "org-a")
    alice = await make_user(db, org, "alice@a.example")
    await db.commit()

    response = await rls_api.as_user(alice).post("/api/v1/projects", json={"name": "P"})
    project_id = response.json()["id"]
    response = await rls_api.as_user(alice).post(
        "/api/v1/documents", json={"title": "Doc", "project_id": project_id}
    )
    document_id = response.json()["id"]
    response = await rls_api.as_user(alice).post(
        f"/api/v1/documents/{document_id}/collaborators",
        json={"email": "newcomer@a.example", "role": "editor"},
    )
    assert response.status_code == 201
    assert response.json()["invite_status"] == "pending"

    # Registration runs on the unprivileged engine, with the real auth router.
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=_auth_app(app_session_factory)), base_url="http://t"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newcomer@a.example",
                "password": "secret123",
                "full_name": "New Comer",
                "organisation": "Org-A",
            },
        )
        assert response.status_code == 201, response.text

    share = (
        await db.execute(
            select(DocumentCollaborator).where(
                DocumentCollaborator.email == "newcomer@a.example"
            )
        )
    ).scalar_one()
    assert share.user_id is not None
    assert share.invite_status == "accepted"


def _auth_app(app_session_factory):
    """The real auth router with the real dependencies, on the app-role engine.

    Nothing is stubbed here — token verification, identity binding and the
    SECURITY DEFINER carve-outs all run exactly as in production; only the
    engine's address differs.
    """
    from app.api import auth as auth_module
    from app.core import deps
    from fastapi import Depends, FastAPI
    from fastapi.security import HTTPAuthorizationCredentials

    app = FastAPI()
    app.include_router(auth_module.router, prefix="/api/v1")

    async def rls_get_session(
        credentials: HTTPAuthorizationCredentials | None = Depends(deps.bearer),
    ):
        info = {}
        if credentials is not None:
            identity = deps._verified_identity(credentials.credentials)
            if identity is not None:
                mode, user_id, claims = identity
                info = {"rls_user_id": str(user_id), "auth_mode": mode, "auth_claims": claims}
        async with app_session_factory(info=info) as session:
            yield session

    app.dependency_overrides[deps.get_session] = rls_get_session
    return app


async def test_legacy_register_login_me_under_rls(db, app_session_factory, legacy_configured):
    """The unauthenticated seams hold: register, duplicate-register, login and
    /auth/me all work as the unprivileged role, with no identity to start from."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=_auth_app(app_session_factory)), base_url="http://t"
    ) as client:
        payload = {
            "email": "founder@acme.example",
            "password": "secret123",
            "full_name": "Founder",
            "organisation": "Acme Inc",
        }
        response = await client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 201, response.text
        token = response.json()["access_token"]

        duplicate = await client.post("/api/v1/auth/register", json=payload)
        assert duplicate.status_code == 409
        assert "already exists" in duplicate.json()["detail"]

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "founder@acme.example", "password": "secret123"},
        )
        assert login.status_code == 200, login.text

        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": "founder@acme.example", "password": "wrong"},
        )
        assert wrong.status_code == 401

        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200
        assert me.json()["email"] == "founder@acme.example"


async def test_supabase_provisioning_under_rls(
    db, app_session_factory, supabase_configured, mint_token
):
    """First sight of a verified Supabase subject provisions the local account —
    an INSERT the policies admit precisely because the row's id is the verified
    token subject."""
    from httpx import ASGITransport, AsyncClient

    subject = uuid.uuid4()
    token = mint_token(sub=subject, email="astro@corp.example")

    async with AsyncClient(
        transport=ASGITransport(app=_auth_app(app_session_factory)), base_url="http://t"
    ) as client:
        for _ in range(2):  # second call takes the already-provisioned path
            response = await client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["id"] == str(subject)

    provisioned = (
        await db.execute(select(User).where(User.id == subject))
    ).scalar_one()
    assert provisioned.email == "astro@corp.example"


async def test_project_filed_assets_follow_the_project_grant(db, app_session_factory):
    """The file_assets policy mirrors the retrieval plane's rule exactly:
    own uploads, or a project the resolver admits — never a colleague's
    private upload, never another organisation's anything."""
    from app.db.models import FileAsset

    from tests.factories import make_file_asset

    org_a = await make_org(db, "org-a")
    org_b = await make_org(db, "org-b")
    alice = await make_user(db, org_a, "alice@a.example")
    carol = await make_user(db, org_a, "carol@a.example")
    bob = await make_user(db, org_b, "bob@b.example")

    project = await make_project(db, org_a, alice)
    filed = await make_file_asset(db, alice, "shared.pdf", project=project)
    private = await make_file_asset(db, alice, "private.pdf")
    await make_grant(
        db,
        org_id=org_a.id,
        subject_type="project",
        subject_id=project.id,
        user=carol,
        role="viewer",
    )
    await db.commit()
    filed_id, private_id = filed.id, private.id

    async with as_user(app_session_factory, carol) as session:
        visible = set((await session.execute(select(FileAsset.id))).scalars().all())
    assert visible == {filed_id}, "a project grant reaches filed assets and nothing else"

    async with as_user(app_session_factory, alice) as session:
        visible = set((await session.execute(select(FileAsset.id))).scalars().all())
    assert visible == {filed_id, private_id}

    async with as_user(app_session_factory, bob) as session:
        assert (await session.execute(select(FileAsset.id))).scalars().all() == []
