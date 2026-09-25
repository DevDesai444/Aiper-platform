"""Retrieval cannot cross a permission boundary.

The property under test: what comes back from the vector store is decided by
the access resolver, not by similarity. Every scenario indexes the *same*
sentence on both sides of a boundary, so a leak would be the top-scoring hit —
if isolation ever regresses, these tests fail loudly, not statistically.

Real Postgres (the resolver is a SQL function), real store code, local
in-process Qdrant, deterministic fake embeddings — identical text embeds to
the identical vector, cosine similarity 1.0.
"""

from __future__ import annotations

import hashlib
import math
import uuid

import pytest_asyncio
from app.agents.skills import SkillContext, build_tools
from app.config import settings
from app.rag import backfill as backfill_module
from app.rag import store
from app.rag.loaders import Page
from app.rag.scope import RetrievalScope, compute_scope
from app.services import permissions
from qdrant_client import AsyncQdrantClient, models

from .factories import make_file_asset, make_grant, make_org, make_project, make_user

SECRET = "The launch code for the orbital platform is exactly twelve digits long."
UNFILED = "Private memo: the acquisition closes on the last Friday of the quarter."

_DIM = 8


def _vec(text: str) -> list[float]:
    """Deterministic unit vector: identical text, identical embedding."""
    digest = hashlib.sha256(text.strip().lower().encode()).digest()
    raw = [b / 255.0 + 0.01 for b in digest[:_DIM]]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


class _FakeEmbeddings:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_vec(t) for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return _vec(text)


@pytest_asyncio.fixture
async def rag(monkeypatch):
    """The real store against a local in-process Qdrant and fake embeddings."""
    local = AsyncQdrantClient(location=":memory:")
    monkeypatch.setattr(store, "_client", local)
    monkeypatch.setattr(store, "_ensured", False)
    monkeypatch.setattr(settings, "embedding_dim", _DIM)
    monkeypatch.setattr(store, "_embeddings", lambda: _FakeEmbeddings())
    yield store
    await local.close()


async def _index(asset, *, text: str) -> None:
    await store.index_pages(
        owner_id=asset.owner_id,
        org_id=asset.org_id,
        project_id=asset.project_id,
        file_id=asset.id,
        filename=asset.filename,
        session_id=None,
        comparison_role="source",
        pages=[Page(page=1, text=text)],
    )


async def _scope(db, user) -> RetrievalScope:
    return await compute_scope(db, user_id=user.id, org_id=user.org_id)


@pytest_asyncio.fixture
async def world(db, rag):
    """Two organisations holding the identical sentence; one project in each org.

    acme: alice owns p_file (in project) and u_file (unfiled); bob has no grants.
    globex: eve owns g_file, unfiled. Both p_file and g_file contain SECRET.
    """
    acme = await make_org(db, "acme")
    globex = await make_org(db, "globex")
    alice = await make_user(db, acme, "alice@acme.test")
    bob = await make_user(db, acme, "bob@acme.test")
    eve = await make_user(db, globex, "eve@globex.test")

    project = await make_project(db, acme, alice, name="Tender")
    await make_grant(
        db, org_id=acme.id, subject_type="project", subject_id=project.id, user=alice, role="owner"
    )

    p_file = await make_file_asset(db, alice, "tender.pdf", project=project)
    u_file = await make_file_asset(db, alice, "memo.pdf")
    g_file = await make_file_asset(db, eve, "rival.pdf")
    await db.commit()

    await _index(p_file, text=SECRET)
    await _index(u_file, text=UNFILED)
    await _index(g_file, text=SECRET)

    return {
        "acme": acme, "globex": globex,
        "alice": alice, "bob": bob, "eve": eve,
        "project": project, "p_file": p_file, "u_file": u_file, "g_file": g_file,
    }


# ────────────────────────────── org boundary ──────────────────────────────


async def test_org_boundary_holds_even_for_identical_text(db, world):
    """The query IS the other org's content, similarity 1.0 — still zero hits.

    A retrieval returns every accessible page at some score, so the property
    is asserted precisely: the foreign copy appears at no rank at all, while
    the caller's own identical copy ranks first.
    """
    hits = await store.search(scope=await _scope(db, world["alice"]), query=SECRET, limit=12)
    assert hits and hits[0].file_id == str(world["p_file"].id)
    assert str(world["g_file"].id) not in {h.file_id for h in hits}

    hits = await store.search(scope=await _scope(db, world["eve"]), query=SECRET, limit=12)
    assert hits and hits[0].file_id == str(world["g_file"].id)
    assert {h.file_id for h in hits} == {str(world["g_file"].id)}


async def test_explicit_foreign_file_ids_cannot_widen_the_scope(db, world):
    """file_ids narrows inside the scope; naming another org's file yields nothing."""
    hits = await store.search(
        scope=await _scope(db, world["alice"]),
        query=SECRET,
        limit=12,
        file_ids=[world["g_file"].id],
    )
    assert hits == []


async def test_read_file_pages_refuses_a_foreign_file(db, world):
    pages = await store.read_file_pages(
        scope=await _scope(db, world["alice"]), file_id=world["g_file"].id
    )
    assert pages == []


# ─────────────────────────── same-org boundaries ──────────────────────────


async def test_same_org_colleague_sees_nothing_without_a_grant(db, world):
    for query in (SECRET, UNFILED):
        assert await store.search(scope=await _scope(db, world["bob"]), query=query, limit=12) == []


async def test_grant_admits_project_files_but_never_unfiled_ones(db, world):
    await make_grant(
        db,
        org_id=world["acme"].id,
        subject_type="project",
        subject_id=world["project"].id,
        user=world["bob"],
        role="viewer",
    )
    await db.commit()

    scope = await _scope(db, world["bob"])
    assert {h.file_id for h in await store.search(scope=scope, query=SECRET, limit=12)} == {
        str(world["p_file"].id)
    }
    # Alice's unfiled memo stays hers alone, grant or no grant: however bob
    # phrases the query, that file appears at no rank.
    unfiled_hits = await store.search(scope=scope, query=UNFILED, limit=12)
    assert str(world["u_file"].id) not in {h.file_id for h in unfiled_hits}
    assert all(UNFILED not in h.text for h in unfiled_hits)


async def test_revocation_is_effective_on_the_next_query(db, world):
    await make_grant(
        db,
        org_id=world["acme"].id,
        subject_type="project",
        subject_id=world["project"].id,
        user=world["bob"],
        role="viewer",
    )
    await db.commit()
    assert await store.search(scope=await _scope(db, world["bob"]), query=SECRET, limit=12) != []

    await permissions.revoke_access(
        db, subject_type="project", subject_id=world["project"].id, user_id=world["bob"].id
    )
    await db.commit()

    # No cache to expire: the very next scope computation reflects the revoke.
    assert await store.search(scope=await _scope(db, world["bob"]), query=SECRET, limit=12) == []


# ──────────────────────────── fail-closed points ──────────────────────────


async def test_point_without_tenancy_payload_is_never_returned(db, world):
    """A pre-migration point matches its owner's query text but has no org_id."""
    alice = world["alice"]
    legacy_text = "Legacy page indexed before the tenancy filter existed."
    legacy_file_id = str(uuid.uuid4())
    await store.client().upsert(
        collection_name=settings.qdrant_collection,
        points=[
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=_vec(legacy_text),
                payload={
                    "owner_id": str(alice.id),
                    "file_id": legacy_file_id,
                    "filename": "legacy.pdf",
                    "page": 1,
                    "text": legacy_text,
                },
            )
        ],
        wait=True,
    )
    hits = await store.search(scope=await _scope(db, alice), query=legacy_text, limit=12)
    assert legacy_file_id not in {h.file_id for h in hits}
    assert all(legacy_text not in h.text for h in hits)


def test_admits_re_verifies_every_hit_against_the_scope():
    """The post-retrieval check: payload must satisfy the scope on its own."""
    org, user, project = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scope = RetrievalScope(org_id=org, user_id=user, project_ids=(project,))

    ok_owner = {"org_id": str(org), "owner_id": str(user), "project_id": ""}
    ok_project = {"org_id": str(org), "owner_id": str(uuid.uuid4()), "project_id": str(project)}
    wrong_org = {"org_id": str(uuid.uuid4()), "owner_id": str(user), "project_id": str(project)}
    no_tenancy = {"owner_id": str(user)}
    foreign = {"org_id": str(org), "owner_id": str(uuid.uuid4()), "project_id": str(uuid.uuid4())}

    assert store._admits(scope, ok_owner)
    assert store._admits(scope, ok_project)
    assert not store._admits(scope, wrong_org)
    assert not store._admits(scope, no_tenancy)
    assert not store._admits(scope, foreign)
    assert not store._admits(scope, None)
    assert not store._admits(RetrievalScope(org_id=None, user_id=user), ok_owner)


# ─────────────────────────────── backfill ─────────────────────────────────


async def test_backfill_restores_visibility_for_pre_tenancy_points(
    db, world, session_factory, monkeypatch
):
    """Enforce first, backfill second: old points reappear only for the right people."""
    alice, bob, eve = world["alice"], world["bob"], world["eve"]
    old_text = "An old page from before the migration, full of figures."
    old_file = await make_file_asset(db, alice, "old.pdf", project=world["project"])
    await db.commit()

    # Indexed the way the pre-tenancy code would have: no org, no project.
    await store.client().upsert(
        collection_name=settings.qdrant_collection,
        points=[
            models.PointStruct(
                id=str(uuid.uuid5(store._NAMESPACE, f"{old_file.id}:1")),
                vector=_vec(old_text),
                payload={
                    "owner_id": str(alice.id),
                    "file_id": str(old_file.id),
                    "filename": old_file.filename,
                    "page": 1,
                    "text": old_text,
                },
            )
        ],
        wait=True,
    )
    old_id = str(old_file.id)
    before = await store.search(scope=await _scope(db, alice), query=old_text, limit=12)
    assert old_id not in {h.file_id for h in before}

    monkeypatch.setattr(backfill_module, "SessionLocal", session_factory)
    stamped = await backfill_module.backfill()
    assert stamped == 4  # every file row in the scenario, idempotently

    after = await store.search(scope=await _scope(db, alice), query=old_text, limit=12)
    assert after and after[0].file_id == old_id
    # Restored for the resolver's audience only — not org-mates, not other orgs.
    for outsider in (bob, eve):
        hits = await store.search(scope=await _scope(db, outsider), query=old_text, limit=12)
        assert old_id not in {h.file_id for h in hits}


# ───────────────────────────── the agent's tools ──────────────────────────


def _ctx_for(user, session_factory, **kwargs) -> SkillContext:
    async def provider() -> RetrievalScope:
        async with session_factory() as scope_db:
            return await compute_scope(scope_db, user_id=user.id, org_id=user.org_id)

    return SkillContext(owner_id=user.id, org_id=user.org_id, scope_provider=provider, **kwargs)


async def test_search_tool_is_scope_bound(db, world, session_factory):
    """The tool closure enforces the boundary even when told to look elsewhere."""
    bob_tools = build_tools(
        _ctx_for(world["bob"], session_factory, attachment_ids=[world["p_file"].id])
    )
    answer = await bob_tools["search_pages"].ainvoke({"query": SECRET})
    assert "No indexed page matched" in answer

    alice_tools = build_tools(
        _ctx_for(world["alice"], session_factory, attachment_ids=[world["p_file"].id])
    )
    answer = await alice_tools["search_pages"].ainvoke({"query": SECRET})
    assert "tender.pdf, p.1" in answer


async def test_search_tool_sees_a_mid_turn_revocation(db, world, session_factory):
    """The scope is recomputed inside the tool call, not once per turn."""
    await make_grant(
        db,
        org_id=world["acme"].id,
        subject_type="project",
        subject_id=world["project"].id,
        user=world["bob"],
        role="viewer",
    )
    await db.commit()

    tools = build_tools(_ctx_for(world["bob"], session_factory))
    assert "tender.pdf" in await tools["search_pages"].ainvoke({"query": SECRET})

    # Same tools object, same turn — the grant disappears between two calls.
    await permissions.revoke_access(
        db, subject_type="project", subject_id=world["project"].id, user_id=world["bob"].id
    )
    await db.commit()
    assert "No indexed page matched" in await tools["search_pages"].ainvoke({"query": SECRET})


async def test_target_tool_refuses_a_foreign_document(db, world, session_factory):
    tools = build_tools(
        _ctx_for(world["alice"], session_factory, target_attachment_id=world["g_file"].id)
    )
    answer = await tools["load_target_document"].ainvoke({})
    assert "no indexed pages" in answer
    assert SECRET not in answer


async def test_default_context_without_an_org_retrieves_nothing(db, world, rag, monkeypatch):
    """A SkillContext built without tenancy fails closed, not open."""
    monkeypatch.setattr(
        "app.db.base.SessionLocal", None
    )  # would explode if the provider ever queried
    ctx = SkillContext(owner_id=world["alice"].id)
    scope = RetrievalScope(org_id=ctx.org_id, user_id=ctx.owner_id)
    assert await store.search(scope=scope, query=SECRET, limit=12) == []


# ───────────────────────── chat request validation ────────────────────────


async def test_build_context_drops_what_the_caller_cannot_read(db, world):
    from app import schemas
    from app.api.chat import _build_context

    request = schemas.ChatRequest(
        message="compare these",
        attachment_ids=[world["p_file"].id, world["g_file"].id],
        target_attachment_id=world["g_file"].id,
    )
    ctx = await _build_context(db, request, world["alice"])
    assert ctx.attachment_ids == [world["p_file"].id]
    assert ctx.target_attachment_id is None
    assert str(world["g_file"].id) not in ctx.filenames


async def test_build_context_keeps_files_shared_through_a_project(db, world):
    from app import schemas
    from app.api.chat import _build_context

    await make_grant(
        db,
        org_id=world["acme"].id,
        subject_type="project",
        subject_id=world["project"].id,
        user=world["bob"],
        role="viewer",
    )
    await db.commit()

    request = schemas.ChatRequest(
        message="summarise", attachment_ids=[world["p_file"].id, world["u_file"].id]
    )
    ctx = await _build_context(db, request, world["bob"])
    # The project file is shared with bob; alice's unfiled memo is not.
    assert ctx.attachment_ids == [world["p_file"].id]
