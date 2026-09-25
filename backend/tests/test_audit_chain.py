"""The tamper-evident records: audit hash chain and revision hashes.

Three properties, each tested at the level it is enforced:

* **Append-only** — UPDATE and DELETE on audit rows raise for every role,
  superuser included; revisions refuse UPDATE the same way.
* **Chained** — every audit row's prev_hash is the previous row's row_hash
  within its organisation; every revision's chain_hash binds its parent's.
* **Verifiable** — the SQL verifiers recompute the chains and name the first
  broken link, including after a rewrite performed with the triggers disabled,
  which is exactly what tampering by a privileged actor looks like.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.db.models import AuditLog, Revision
from app.services.audit import record_audit
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.conftest import as_user
from tests.factories import make_document, make_grant, make_org, make_project, make_user

GENESIS = "0" * 64


async def _tenant(db, slug="org-a", email="alice@a.example"):
    org = await make_org(db, slug)
    user = await make_user(db, org, email)
    project = await make_project(db, org, user)
    document = await make_document(db, user, project)
    await make_grant(
        db,
        org_id=org.id,
        subject_type="document",
        subject_id=document.id,
        user=user,
        role="owner",
    )
    await db.commit()
    return org, user, project, document


# ────────────────────────────────── audit log ────────────────────────────────


async def test_audit_rows_chain_per_organisation(db, app_session_factory):
    """prev_hash linkage is per organisation: each tenant's history is one
    self-contained chain starting at genesis."""
    org_a, alice, _, document_a = await _tenant(db)
    org_b, bob, _, document_b = await _tenant(db, "org-b", "bob@b.example")

    async with as_user(app_session_factory, alice) as session:
        for n in (1, 2):
            await record_audit(
                session,
                org_id=org_a.id,
                actor_id=alice.id,
                action="document.commit",
                subject_type="document",
                subject_id=document_a.id,
                payload={"n": n},
            )
        await session.commit()
    async with as_user(app_session_factory, bob) as session:
        await record_audit(
            session,
            org_id=org_b.id,
            actor_id=bob.id,
            action="document.create",
            subject_type="document",
            subject_id=document_b.id,
            payload={},
        )
        await session.commit()

    a_rows = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.org_id == org_a.id).order_by(AuditLog.seq)
            )
        )
        .scalars()
        .all()
    )
    b_rows = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.org_id == org_b.id).order_by(AuditLog.seq)
            )
        )
        .scalars()
        .all()
    )
    assert [r.prev_hash for r in a_rows] == [GENESIS, a_rows[0].row_hash]
    assert [r.prev_hash for r in b_rows] == [GENESIS]
    assert len({r.row_hash for r in a_rows + b_rows}) == 3  # all distinct


async def test_the_app_role_can_only_append_its_own_events(db, app_session_factory):
    """Write-only, own-org, own-name: everything else about audit_log is shut
    for the runtime role."""
    org_a, alice, _, document = await _tenant(db)
    org_b, bob, _, _ = await _tenant(db, "org-b", "bob@b.example")

    async with as_user(app_session_factory, alice) as session:
        # Another organisation's chain is unappendable…
        with pytest.raises(ProgrammingError, match="row-level security"):
            await record_audit(
                session,
                org_id=org_b.id,
                actor_id=alice.id,
                action="forged",
                subject_type="document",
                subject_id=document.id,
            )
        await session.rollback()
        # …and so is another actor's name.
        with pytest.raises(ProgrammingError, match="row-level security"):
            await record_audit(
                session,
                org_id=org_a.id,
                actor_id=bob.id,
                action="framed",
                subject_type="document",
                subject_id=document.id,
            )
        await session.rollback()
        # Reading it back is not a thing the runtime role can do at all.
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(select(AuditLog))
        await session.rollback()


async def test_audit_log_is_append_only_for_everyone(db, app_session_factory):
    """UPDATE and DELETE raise — as the app role and as the superuser alike."""
    org, alice, _, document = await _tenant(db)
    async with as_user(app_session_factory, alice) as session:
        await record_audit(
            session,
            org_id=org.id,
            actor_id=alice.id,
            action="document.create",
            subject_type="document",
            subject_id=document.id,
        )
        await session.commit()

    # The superuser owns the table and still cannot rewrite history quietly.
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("UPDATE audit_log SET action = 'forged'"))
    await db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("DELETE FROM audit_log"))
    await db.rollback()


async def test_verification_walks_the_chain_and_names_the_break(db, app_session_factory):
    """An intact chain verifies; a row rewritten with the trigger disabled —
    tampering as a privileged actor would actually do it — is reported by id,
    and rows after the break keep their place."""
    org, alice, _, document = await _tenant(db)
    async with as_user(app_session_factory, alice) as session:
        for n in range(3):
            await record_audit(
                session,
                org_id=org.id,
                actor_id=alice.id,
                action="document.commit",
                subject_type="document",
                subject_id=document.id,
                payload={"n": n},
            )
        await session.commit()

        verdict = (
            (await session.execute(text("SELECT * FROM aiper_audit_verify(:org)"), {"org": str(org.id)}))
            .mappings()
            .one()
        )
        assert verdict["ok"] is True
        assert verdict["checked"] == 3

    # Rewrite the middle row behind the trigger's back.
    target = (
        await db.execute(select(AuditLog).where(AuditLog.org_id == org.id).order_by(AuditLog.seq))
    ).scalars().all()[1]
    await db.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update"))
    await db.execute(
        text("UPDATE audit_log SET payload = '{\"n\": 999}' WHERE id = :id"),
        {"id": target.id},
    )
    await db.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update"))
    await db.commit()

    async with as_user(app_session_factory, alice) as session:
        verdict = (
            (await session.execute(text("SELECT * FROM aiper_audit_verify(:org)"), {"org": str(org.id)}))
            .mappings()
            .one()
        )
    assert verdict["ok"] is False
    assert verdict["first_broken_id"] == target.id
    assert verdict["checked"] == 1  # exactly the rows before the break


async def test_verification_answers_only_for_ones_own_organisation(db, app_session_factory):
    """The verifier gates on the session identity inside SQL: asking about a
    foreign organisation returns nothing, not even 'intact'."""
    org_a, alice, _, _ = await _tenant(db)
    _, bob, _, _ = await _tenant(db, "org-b", "bob@b.example")

    async with as_user(app_session_factory, bob) as session:
        rows = (
            await session.execute(text("SELECT * FROM aiper_audit_verify(:org)"), {"org": str(org_a.id)})
        ).all()
    assert rows == []


async def test_concurrent_appends_keep_one_linear_chain(db, app_session_factory):
    """The per-organisation advisory lock serialises writers: parallel inserts
    land as one unbroken chain, whatever their arrival order."""
    org, alice, _, document = await _tenant(db)

    async def append(n: int) -> None:
        async with as_user(app_session_factory, alice) as session:
            await record_audit(
                session,
                org_id=org.id,
                actor_id=alice.id,
                action="document.commit",
                subject_type="document",
                subject_id=document.id,
                payload={"n": n},
            )
            await session.commit()

    await asyncio.gather(*(append(n) for n in range(5)))

    async with as_user(app_session_factory, alice) as session:
        verdict = (
            (await session.execute(text("SELECT * FROM aiper_audit_verify(:org)"), {"org": str(org.id)}))
            .mappings()
            .one()
        )
    assert verdict["ok"] is True
    assert verdict["checked"] == 5


# ───────────────────────────── revision hashes ───────────────────────────────


async def test_revisions_hash_their_content_and_their_parent(db, rls_api):
    """Every commit through the real routes lands hashed and linked."""
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
        f"/api/v1/documents/{document_id}/commits",
        json={
            "content_json": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "v2"}]}
            ]},
            "commit_message": "second",
        },
    )
    assert response.status_code == 200, response.text

    revisions = (
        (
            await db.execute(
                select(Revision)
                .where(Revision.document_id == uuid.UUID(document_id))
                .order_by(Revision.revision_number)
            )
        )
        .scalars()
        .all()
    )
    assert len(revisions) == 2
    assert all(len(r.content_hash) == 64 for r in revisions)
    assert all(len(r.chain_hash) == 64 for r in revisions)
    assert revisions[0].chain_hash != revisions[1].chain_hash

    verdict = await rls_api.as_user(alice).get(f"/api/v1/audit/documents/{document_id}/verify")
    assert verdict.status_code == 200
    assert verdict.json() == {"ok": True, "checked": 2, "first_broken_revision": None}


async def test_a_tampered_revision_is_named_by_the_verifier(db, rls_api):
    """Rewriting a revision behind the trigger's back breaks its content hash,
    and verification points at exactly that revision."""
    org = await make_org(db, "org-a")
    alice = await make_user(db, org, "alice@a.example")
    await db.commit()

    response = await rls_api.as_user(alice).post("/api/v1/projects", json={"name": "P"})
    project_id = response.json()["id"]
    response = await rls_api.as_user(alice).post(
        "/api/v1/documents", json={"title": "Doc", "project_id": project_id}
    )
    document_id = response.json()["id"]

    first = (
        await db.execute(
            select(Revision).where(Revision.document_id == uuid.UUID(document_id))
        )
    ).scalar_one()

    await db.execute(text("ALTER TABLE revisions DISABLE TRIGGER revisions_no_update"))
    await db.execute(
        text("UPDATE revisions SET content_text = 'rewritten history' WHERE id = :id"),
        {"id": str(first.id)},
    )
    await db.execute(text("ALTER TABLE revisions ENABLE TRIGGER revisions_no_update"))
    await db.commit()

    verdict = await rls_api.as_user(alice).get(f"/api/v1/audit/documents/{document_id}/verify")
    assert verdict.status_code == 200
    body = verdict.json()
    assert body["ok"] is False
    assert body["first_broken_revision"] == str(first.id)


async def test_revisions_refuse_update_for_everyone(db, app_session_factory):
    """History is written once: the superuser hits the trigger, the app role's
    UPDATE matches nothing (revisions have no UPDATE policy at all)."""
    org, alice, _, document = await _tenant(db)
    # Captured up front: the raises/rollback below expires the ORM instances.
    alice_id = alice.id
    await db.execute(
        text(
            "INSERT INTO revisions (id, document_id, revision_number, commit_message,"
            " source, author_id, author_email, author_name, content_json, content_text, created_at)"
            " VALUES (gen_random_uuid(), :doc, 1, 'first', 'human', :author,"
            " 'alice@a.example', 'Alice', '{}', 'original', now())"
        ),
        {"doc": str(document.id), "author": str(alice.id)},
    )
    await db.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("UPDATE revisions SET content_text = 'forged'"))
    await db.rollback()

    # The app role fails one layer earlier still: it holds no UPDATE privilege
    # on revisions at all.
    async with as_user(app_session_factory, alice_id) as session:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(text("UPDATE revisions SET content_text = 'forged'"))
        await session.rollback()

    content = (await db.execute(select(Revision.content_text))).scalar_one()
    assert content == "original"


async def test_verifier_says_nothing_about_unreachable_documents(db, app_session_factory):
    """Same discipline as everywhere else: no access, no answer — not even
    'this document has zero revisions'."""
    _, _, _, document = await _tenant(db)
    _, bob, _, _ = await _tenant(db, "org-b", "bob@b.example")

    async with as_user(app_session_factory, bob) as session:
        rows = (
            await session.execute(
                text("SELECT * FROM aiper_revision_verify(:doc)"), {"doc": str(document.id)}
            )
        ).all()
    assert rows == []
