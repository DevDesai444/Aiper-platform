"""Comment routes: what the resolver's answer becomes for `/comments`.

Same conventions as ``test_routes.py``: real Postgres, real routers, an
in-process `api` fixture that impersonates a user. Scenarios are seeded through
the factories.

The routes exercised here are the E6 addition described in
``app/api/documents.py::comments`` and ``0005_document_comments.py``.

One test near the bottom (``test_comment_lifecycle_under_row_level_security``)
uses ``rls_api`` instead of ``api`` — the same routes, but over a session
connected as the unprivileged ``aiper_app`` role with migration 0003/0004's
row-level security live underneath. 0005 landed before E3's RLS migrations
existed and had to gain its own GRANT + policy block when this branch rebased
past them (0003 enumerates the tables it protects at the time IT runs, so a
table created later needs to add itself). Everything above this line runs
against the superuser-bypass ``api`` fixture, which would pass even if that
wiring were missing entirely — this one test is what actually proves it works.
"""

from __future__ import annotations

import uuid

from app.services.permissions import grant_access

from tests.factories import make_document, make_org, make_project, make_user

# ────────────────────────────── scenario helpers ──────────────────────────────


async def _seed_document(db):
    """Alice (owner) + Bob (editor) + Charlie (viewer) + Mallory (other org).

    Everyone in `org` has the corresponding grant on `document`. Nothing is
    granted to Mallory: another org, no access at all, must always look like
    the document does not exist.
    """
    org = await make_org(db, "org")
    other_org = await make_org(db, "other")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")
    charlie = await make_user(db, org, "charlie@org.example")
    mallory = await make_user(db, other_org, "mallory@other.example")

    project = await make_project(db, org, alice)
    document = await make_document(db, alice, project)

    await grant_access(
        db, org_id=org.id, subject_type="document",
        subject_id=document.id, user_id=alice.id, role="owner",
    )
    await grant_access(
        db, org_id=org.id, subject_type="document",
        subject_id=document.id, user_id=bob.id, role="editor",
    )
    await grant_access(
        db, org_id=org.id, subject_type="document",
        subject_id=document.id, user_id=charlie.id, role="viewer",
    )
    await db.commit()
    return {
        "org": org,
        "alice": alice,
        "bob": bob,
        "charlie": charlie,
        "mallory": mallory,
        "project": project,
        "document": document,
    }


def _url(document_id: uuid.UUID, suffix: str = "") -> str:
    return f"/api/v1/documents/{document_id}/comments{suffix}"


# ────────────────────────────── access enforcement ────────────────────────────


async def test_viewer_can_list_but_not_create(api, db):
    s = await _seed_document(db)

    # First, editor Bob adds a comment so there is something to see.
    r = await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "m1", "body": "seeded", "quoted_text": "some text"},
    )
    assert r.status_code == 201, r.text

    # Charlie (viewer) can list.
    r = await api.as_user(s["charlie"]).get(_url(s["document"].id))
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1

    # But Charlie cannot post.
    r = await api.as_user(s["charlie"]).post(
        _url(s["document"].id),
        json={"mark_id": "m2", "body": "nope", "quoted_text": ""},
    )
    assert r.status_code == 403


async def test_cross_org_user_gets_404_on_every_endpoint(api, db):
    s = await _seed_document(db)
    doc_id = s["document"].id
    mallory = s["mallory"]

    # A 404 (not 403) on every one: an unreachable subject must be
    # indistinguishable from one that does not exist.
    responses = [
        await api.as_user(mallory).get(_url(doc_id)),
        await api.as_user(mallory).post(
            _url(doc_id),
            json={"mark_id": "m", "body": "x", "quoted_text": ""},
        ),
        await api.as_user(mallory).post(_url(doc_id, "/m/resolve")),
        await api.as_user(mallory).delete(_url(doc_id, "/m")),
    ]
    for r in responses:
        assert r.status_code == 404, r.text


# ─────────────────────────────── list + create ────────────────────────────────


async def test_create_returns_the_row_and_list_orders_oldest_first(api, db):
    s = await _seed_document(db)

    first = await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "m1", "body": "first", "quoted_text": "quote one"},
    )
    assert first.status_code == 201
    body = first.json()
    # v1 envelope: the row itself is returned on POST.
    assert body["mark_id"] == "m1"
    assert body["body"] == "first"
    assert body["quoted_text"] == "quote one"
    # Denormalised author display fields carry the poster's identity.
    assert body["author_email"] == s["bob"].email
    assert body["resolved_at"] is None

    # Second comment on the same thread.
    second = await api.as_user(s["alice"]).post(
        _url(s["document"].id),
        json={"mark_id": "m1", "body": "reply", "quoted_text": "quote one"},
    )
    assert second.status_code == 201

    # And a comment on a DIFFERENT thread — the list must include both.
    third = await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "m2", "body": "elsewhere", "quoted_text": ""},
    )
    assert third.status_code == 201

    r = await api.as_user(s["charlie"]).get(_url(s["document"].id))
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["body"] for i in items] == ["first", "reply", "elsewhere"]
    # v1 envelope shape preserved:
    assert set(r.json().keys()) == {"items"}


# ──────────────────────────────── resolve ─────────────────────────────────────


async def test_resolve_marks_every_comment_in_the_thread(api, db):
    s = await _seed_document(db)

    for body in ("first", "second"):
        r = await api.as_user(s["bob"]).post(
            _url(s["document"].id),
            json={"mark_id": "m", "body": body, "quoted_text": ""},
        )
        assert r.status_code == 201

    # A comment on a DIFFERENT thread is not affected.
    other = await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "other", "body": "untouched", "quoted_text": ""},
    )
    assert other.status_code == 201

    r = await api.as_user(s["alice"]).post(_url(s["document"].id, "/m/resolve"))
    assert r.status_code == 200, r.text
    payload = r.json()
    # v1 envelope shape preserved: {"comments": [...]}.
    assert set(payload.keys()) == {"comments"}
    assert len(payload["comments"]) == 2
    assert all(c["resolved_at"] is not None for c in payload["comments"])

    # The unrelated thread is still unresolved.
    listing = await api.as_user(s["charlie"]).get(_url(s["document"].id))
    kept = {i["mark_id"]: i["resolved_at"] for i in listing.json()["items"]}
    assert kept["other"] is None


async def test_resolve_is_idempotent(api, db):
    s = await _seed_document(db)
    await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "m", "body": "one", "quoted_text": ""},
    )

    first = await api.as_user(s["bob"]).post(_url(s["document"].id, "/m/resolve"))
    ts = first.json()["comments"][0]["resolved_at"]

    # A second resolve leaves the row alone — the timestamp does not move.
    second = await api.as_user(s["bob"]).post(_url(s["document"].id, "/m/resolve"))
    assert second.status_code == 200
    assert second.json()["comments"][0]["resolved_at"] == ts


async def test_resolve_unknown_thread_is_404(api, db):
    s = await _seed_document(db)
    r = await api.as_user(s["alice"]).post(
        _url(s["document"].id, "/does-not-exist/resolve")
    )
    assert r.status_code == 404


async def test_viewer_cannot_resolve(api, db):
    s = await _seed_document(db)
    await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "m", "body": "one", "quoted_text": ""},
    )
    r = await api.as_user(s["charlie"]).post(_url(s["document"].id, "/m/resolve"))
    assert r.status_code == 403


# ──────────────────────────────── delete ──────────────────────────────────────


async def test_author_can_delete_their_own_thread(api, db):
    s = await _seed_document(db)
    await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "mine", "body": "hi", "quoted_text": ""},
    )
    r = await api.as_user(s["bob"]).delete(_url(s["document"].id, "/mine"))
    assert r.status_code == 204

    listing = await api.as_user(s["charlie"]).get(_url(s["document"].id))
    assert listing.json()["items"] == []


async def test_non_author_editor_cannot_delete_someone_elses_thread(api, db):
    s = await _seed_document(db)
    # Alice authored — but she's owner. Introduce a second editor whose
    # authorship distinguishes them from Bob.
    dave = await make_user(db, s["org"], "dave@org.example")
    await grant_access(
        db, org_id=s["org"].id, subject_type="document",
        subject_id=s["document"].id, user_id=dave.id, role="editor",
    )
    await db.commit()

    await api.as_user(dave).post(
        _url(s["document"].id),
        json={"mark_id": "d", "body": "dave's", "quoted_text": ""},
    )

    # Bob is an editor (not owner) and did NOT author — 403.
    r = await api.as_user(s["bob"]).delete(_url(s["document"].id, "/d"))
    assert r.status_code == 403


async def test_owner_can_delete_any_thread(api, db):
    s = await _seed_document(db)

    await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "b", "body": "bob wrote", "quoted_text": ""},
    )
    r = await api.as_user(s["alice"]).delete(_url(s["document"].id, "/b"))
    assert r.status_code == 204


async def test_editor_partial_authorship_cannot_delete(api, db):
    """A thread with one Bob comment and one Alice comment is only deletable
    by the owner: Bob can't delete because Alice's comment isn't his."""
    s = await _seed_document(db)

    await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "shared", "body": "bob", "quoted_text": ""},
    )
    await api.as_user(s["alice"]).post(
        _url(s["document"].id),
        json={"mark_id": "shared", "body": "alice", "quoted_text": ""},
    )

    r = await api.as_user(s["bob"]).delete(_url(s["document"].id, "/shared"))
    assert r.status_code == 403


async def test_delete_unknown_thread_is_404(api, db):
    s = await _seed_document(db)
    r = await api.as_user(s["alice"]).delete(
        _url(s["document"].id, "/does-not-exist")
    )
    assert r.status_code == 404


# ──────────────────────────── payload validation ──────────────────────────────


async def test_body_required(api, db):
    s = await _seed_document(db)
    r = await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "m", "body": "", "quoted_text": ""},
    )
    assert r.status_code == 422


async def test_mark_id_required(api, db):
    s = await _seed_document(db)
    r = await api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "", "body": "hi", "quoted_text": ""},
    )
    assert r.status_code == 422


# ───────────────────────────── row-level security ──────────────────────────────


async def test_comment_lifecycle_under_row_level_security(rls_api, db):
    """The same routes, over the real ``aiper_app`` role, RLS policies live.

    Proves 0005's GRANT + CREATE POLICY block (added on rebase past E3's RLS
    migrations) actually lets the feature work end-to-end — not just that the
    migration ran without a SQL error. Every ``api``-fixture test above this
    line would still pass even if that block were deleted outright, because
    the superuser connection those use bypasses row security entirely.
    """
    s = await _seed_document(db)

    create = await rls_api.as_user(s["bob"]).post(
        _url(s["document"].id),
        json={"mark_id": "m1", "body": "hello", "quoted_text": "hi"},
    )
    assert create.status_code == 201, create.text

    listing = await rls_api.as_user(s["charlie"]).get(_url(s["document"].id))
    assert listing.status_code == 200, listing.text
    assert [i["body"] for i in listing.json()["items"]] == ["hello"]

    # A viewer's create is refused by the application check before any SQL
    # runs — this is the same 403 as the api-fixture test above, now reached
    # through a connection where the INSERT policy would also refuse it.
    forbidden = await rls_api.as_user(s["charlie"]).post(
        _url(s["document"].id),
        json={"mark_id": "m2", "body": "nope", "quoted_text": ""},
    )
    assert forbidden.status_code == 403

    resolve = await rls_api.as_user(s["alice"]).post(_url(s["document"].id, "/m1/resolve"))
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["comments"][0]["resolved_at"] is not None

    # Mallory's org has no grant at all — the document (and therefore its
    # comments) is a 404, indistinguishable from one that does not exist.
    outside = await rls_api.as_user(s["mallory"]).get(_url(s["document"].id))
    assert outside.status_code == 404

    delete = await rls_api.as_user(s["alice"]).delete(_url(s["document"].id, "/m1"))
    assert delete.status_code == 204, delete.text

    after = await rls_api.as_user(s["charlie"]).get(_url(s["document"].id))
    assert after.json()["items"] == []
