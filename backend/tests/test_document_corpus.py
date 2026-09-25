"""What the platform itself produces, made searchable — and kept inside its
permission boundary.

Three layers under test:

* the walker (`chunk_document`) — pure, no I/O: ProseMirror `content_json` in,
  `Page`-shaped chunks out, one per heading section (further windowed if a
  section runs long).
* the store (`index_document`, `search_documents`) — real Postgres, a local
  in-process Qdrant, deterministic fake embeddings: the same harness
  `test_rag_isolation.py` uses for files, extended with the document-specific
  SQL post-verify (`app.rag.scope.admitted_document_ids`).
* the backfill CLI — the one-time reindex of documents committed before this
  unit shipped.

Access-boundary tests always index the *identical* secret text on both sides
of a boundary, so a leak would be the top-scoring hit — a regression fails
loudly, not statistically.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

import pytest_asyncio
from app.config import settings
from app.db.models import Document
from app.rag import backfill_documents as backfill_documents_module
from app.rag import store
from app.rag.corpus import chunk_document, reindex_document
from app.rag.loaders import Page
from app.rag.scope import RetrievalScope, admitted_document_ids, compute_scope
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import select

from tests.conftest import anonymous

from .factories import make_document, make_grant, make_org, make_project, make_user

SECRET = "The launch code for the orbital platform is exactly twelve digits long."

_DIM = 8


# ═══════════════════════════ ProseMirror builders ══════════════════════════


def _doc(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "content": list(content)}


def _heading(text: str, level: int = 1) -> dict[str, Any]:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def _p(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}] if text else []}


def _bullet_list(*items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": list(item)} for item in items],
    }


def _ordered_list(*items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "orderedList",
        "content": [{"type": "listItem", "content": list(item)} for item in items],
    }


def _blockquote(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "blockquote", "content": list(content)}


def _table(*rows: list[str]) -> dict[str, Any]:
    return {
        "type": "table",
        "content": [
            {
                "type": "tableRow",
                "content": [{"type": "tableCell", "content": [_p(cell)]} for cell in row],
            }
            for row in rows
        ],
    }


# ═══════════════════════════════ the walker ════════════════════════════════


def test_heading_starts_a_new_section():
    content = _doc(_heading("Intro"), _p("hello"), _heading("Scope"), _p("world"))
    pages = chunk_document(content)
    assert [p.text for p in pages] == ["# Intro\n\nhello", "# Scope\n\nworld"]
    assert [p.page for p in pages] == [1, 2]


def test_flat_sectioning_ignores_heading_level():
    """An H2 immediately after an H1 is its own section, not nested under it
    — see corpus.py's `_sections` docstring for why flat beats hierarchical."""
    content = _doc(_heading("Top", level=1), _heading("Sub", level=2), _p("body"))
    pages = chunk_document(content)
    assert [p.text for p in pages] == ["# Top", "## Sub\n\nbody"]


def test_preamble_before_any_heading_is_its_own_chunk():
    content = _doc(_p("no heading yet"), _heading("Later"), _p("after"))
    pages = chunk_document(content)
    assert pages[0].text == "no heading yet"
    assert pages[1].text == "# Later\n\nafter"


def test_nested_lists_are_indented_under_their_marker():
    content = _doc(
        _bullet_list(
            [_p("first")],
            [_p("second"), _ordered_list([_p("nested one")], [_p("nested two")])],
        )
    )
    pages = chunk_document(content)
    assert pages[0].text.split("\n") == [
        "- first",
        "- second",
        "  1. nested one",
        "  2. nested two",
    ]


def test_table_renders_rows_as_pipe_separated_cells():
    content = _doc(_table(["Name", "Role"], ["Alice", "Owner"], ["Bob", "Viewer"]))
    pages = chunk_document(content)
    assert pages[0].text.split("\n") == ["Name | Role", "Alice | Owner", "Bob | Viewer"]


def test_blockquote_keeps_each_paragraph_on_its_own_line():
    content = _doc(_blockquote(_p("first quoted paragraph"), _p("second quoted paragraph")))
    pages = chunk_document(content)
    assert pages[0].text.split("\n") == [
        "> first quoted paragraph",
        "> second quoted paragraph",
    ]


def test_hard_break_separates_text_runs_with_a_newline():
    para = {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "line one"},
            {"type": "hardBreak"},
            {"type": "text", "text": "line two"},
        ],
    }
    pages = chunk_document(_doc(para))
    assert pages[0].text == "line one\nline two"


def test_empty_document_yields_no_chunks():
    assert chunk_document(None) == []
    assert chunk_document({"type": "doc", "content": []}) == []
    assert chunk_document(_doc(_p(""))) == []


def test_unknown_node_type_falls_back_to_best_effort_text():
    node = {"type": "someFutureExtension", "content": [{"type": "text", "text": "still readable"}]}
    pages = chunk_document(_doc(node))
    assert pages[0].text == "still readable"


def test_short_section_is_a_single_chunk():
    content = _doc(_heading("Short"), _p("just one short paragraph"))
    assert len(chunk_document(content)) == 1


def test_long_section_is_split_into_windows_that_each_carry_the_heading():
    paragraphs = [
        _p(f"Paragraph number {i} with enough padding text to push the total length up.")
        for i in range(60)
    ]
    content = _doc(_heading("Long Section"), *paragraphs)
    pages = chunk_document(content)
    assert len(pages) > 1
    assert all(p.text.startswith("# Long Section") for p in pages)
    assert [p.page for p in pages] == list(range(1, len(pages) + 1))
    # Nothing from the section was dropped by the windowing.
    joined = "\n".join(p.text for p in pages)
    assert all(f"Paragraph number {i}" in joined for i in range(60))


# ═══════════════════════ store / hook (real DB, fake Qdrant) ═══════════════


def _vec(text: str) -> list[float]:
    """Deterministic unit vector: identical text, identical embedding —
    mirrors test_rag_isolation.py's fixture exactly."""
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
    local = AsyncQdrantClient(location=":memory:")
    monkeypatch.setattr(store, "_client", local)
    monkeypatch.setattr(store, "_ensured", False)
    monkeypatch.setattr(settings, "embedding_dim", _DIM)
    monkeypatch.setattr(store, "_embeddings", lambda: _FakeEmbeddings())
    yield store
    await local.close()


async def _document_points(document_id: uuid.UUID) -> list:
    # A dry run, or a test that only asserts absence, may never have caused
    # the collection to exist at all — that is "no points", the same as an
    # empty scroll, not a different case the caller should have to handle.
    await store.ensure_collection()
    points, _ = await store.client().scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=str(document_id))
                )
            ]
        ),
        limit=100,
        with_payload=True,
    )
    return points


async def _scope(db, user) -> RetrievalScope:
    return await compute_scope(db, user_id=user.id, org_id=user.org_id)


async def test_index_document_shrinks_cleanly_on_delete_then_upsert(db, rag):
    """Upserting alone would leave a shorter revision's dropped tail behind —
    delete-first is what actually keeps the index matching the content."""
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="Spec")
    await db.commit()

    long_pages = [Page(page=i, text=f"chunk {i} content") for i in range(1, 6)]
    count = await store.index_document(
        document_id=document.id, org_id=org.id, project_id=project.id,
        owner_id=user.id, title="Spec", pages=long_pages,
    )
    assert count == 5
    assert len(await _document_points(document.id)) == 5

    short_pages = [Page(page=1, text="only one chunk now")]
    count = await store.index_document(
        document_id=document.id, org_id=org.id, project_id=project.id,
        owner_id=user.id, title="Spec", pages=short_pages,
    )
    assert count == 1
    remaining = await _document_points(document.id)
    assert len(remaining) == 1
    assert remaining[0].payload["text"] == "only one chunk now"


async def test_index_document_is_idempotent_for_unchanged_content(db, rag):
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="Spec")
    await db.commit()

    pages = [Page(page=1, text="stable content")]
    kwargs = dict(
        document_id=document.id, org_id=org.id, project_id=project.id,
        owner_id=user.id, title="Spec", pages=pages,
    )
    await store.index_document(**kwargs)
    ids_first = {p.id for p in await _document_points(document.id)}

    await store.index_document(**kwargs)
    ids_second = {p.id for p in await _document_points(document.id)}
    assert ids_first == ids_second
    assert len(ids_first) == 1


async def test_index_document_with_no_pages_clears_existing_points(db, rag):
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="Spec")
    await db.commit()

    await store.index_document(
        document_id=document.id, org_id=org.id, project_id=project.id,
        owner_id=user.id, title="Spec", pages=[Page(page=1, text="was here")],
    )
    assert len(await _document_points(document.id)) == 1

    count = await store.index_document(
        document_id=document.id, org_id=org.id, project_id=project.id,
        owner_id=user.id, title="Spec", pages=[],
    )
    assert count == 0
    assert await _document_points(document.id) == []


async def test_reindex_document_hook_chunks_the_current_content_json(db, rag):
    """The commit-time hook — the exact function `_commit()` calls."""
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="Spec")
    document.content_json = _doc(_heading("Overview"), _p("First revision."))
    await db.commit()

    await reindex_document(document)
    points = await _document_points(document.id)
    assert len(points) == 1
    assert "First revision." in points[0].payload["text"]
    assert points[0].payload["title"] == "Spec"
    assert points[0].payload["source"] == "document"

    # A second commit that empties the content leaves no chunk behind —
    # proving the hook is delete-first, not just index_document in isolation.
    document.content_json = _doc()
    await reindex_document(document)
    assert await _document_points(document.id) == []


async def test_reindex_document_never_raises_on_an_indexing_failure(db, rag, monkeypatch):
    """A commit must never fail because indexing hiccuped."""
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="Spec")
    document.content_json = _doc(_p("hello"))
    await db.commit()

    async def boom(**kwargs):
        raise RuntimeError("qdrant is down")

    monkeypatch.setattr(store, "index_document", boom)
    await reindex_document(document)  # must not raise


# ─────────────────────────── cross-tenant retrieval ────────────────────────


@pytest_asyncio.fixture
async def doc_world(db, rag):
    """acme: alice owns a project-filed, project-granted document (p_doc) and
    directly shares it with carol at the document level only — carol has no
    project or folder grant at all. bob has nothing. globex: eve owns her own
    project-filed document (g_doc). p_doc and g_doc hold the identical SECRET.
    """
    acme = await make_org(db, "acme")
    globex = await make_org(db, "globex")
    alice = await make_user(db, acme, "alice@acme.test")
    bob = await make_user(db, acme, "bob@acme.test")
    carol = await make_user(db, acme, "carol@acme.test")
    eve = await make_user(db, globex, "eve@globex.test")

    acme_project = await make_project(db, acme, alice, name="Tender")
    await make_grant(
        db, org_id=acme.id, subject_type="project", subject_id=acme_project.id,
        user=alice, role="owner",
    )
    p_doc = await make_document(db, alice, acme_project, title="Proposal")
    await make_grant(
        db, org_id=acme.id, subject_type="document", subject_id=p_doc.id,
        user=alice, role="owner",
    )
    await make_grant(
        db, org_id=acme.id, subject_type="document", subject_id=p_doc.id,
        user=carol, role="viewer",
    )

    globex_project = await make_project(db, globex, eve, name="Rival")
    await make_grant(
        db, org_id=globex.id, subject_type="project", subject_id=globex_project.id,
        user=eve, role="owner",
    )
    g_doc = await make_document(db, eve, globex_project, title="Rival proposal")
    await make_grant(
        db, org_id=globex.id, subject_type="document", subject_id=g_doc.id,
        user=eve, role="owner",
    )
    await db.commit()

    for document in (p_doc, g_doc):
        await store.index_document(
            document_id=document.id, org_id=document.org_id, project_id=document.project_id,
            owner_id=document.owner_id, title=document.title, pages=[Page(page=1, text=SECRET)],
        )

    return {
        "acme": acme, "globex": globex, "alice": alice, "bob": bob, "carol": carol, "eve": eve,
        "p_doc": p_doc, "g_doc": g_doc,
    }


async def test_project_granted_user_reads_the_project_filed_document(db, doc_world):
    hits = await store.search_documents(
        db=db, scope=await _scope(db, doc_world["alice"]), query=SECRET, limit=12
    )
    assert hits and hits[0].file_id == str(doc_world["p_doc"].id)


async def test_org_boundary_holds_even_for_identical_document_text(db, doc_world):
    hits = await store.search_documents(
        db=db, scope=await _scope(db, doc_world["eve"]), query=SECRET, limit=12
    )
    assert hits and hits[0].file_id == str(doc_world["g_doc"].id)
    assert str(doc_world["p_doc"].id) not in {h.file_id for h in hits}


async def test_colleague_without_any_grant_sees_nothing(db, doc_world):
    hits = await store.search_documents(
        db=db, scope=await _scope(db, doc_world["bob"]), query=SECRET, limit=12
    )
    assert hits == []


async def test_direct_document_grant_without_project_access_is_the_accepted_gap(db, doc_world):
    """carol holds an explicit document-level share and nothing broader — the
    resolver admits her, but project-level access does not follow from a
    narrower grant, so her scope never carries the document's project and
    the coarse Qdrant filter drops the point before the SQL check runs.
    Documented, accepted limitation for this unit (see store.py's module
    docstring on document retrieval) — this asserts today's actual behaviour,
    not that it should never change."""
    scope = await _scope(db, doc_world["carol"])
    assert await store.search_documents(db=db, scope=scope, query=SECRET, limit=12) == []

    # Proof it is a coarse-filter gap, not a resolver gap: asked directly,
    # the SQL layer says yes.
    allowed = await admitted_document_ids(
        db, user_id=doc_world["carol"].id, document_ids=[doc_world["p_doc"].id]
    )
    assert doc_world["p_doc"].id in allowed


async def test_sql_check_catches_a_stale_project_id_in_the_payload(db, rag):
    """The coarse filter trusts the payload's project_id. If a document's
    points were indexed while it sat in a different project than it lives in
    now, the coarse filter alone would still admit the old audience — the SQL
    post-verify re-derives access from the live row and catches it."""
    acme = await make_org(db, "acme")
    alice = await make_user(db, acme, "alice@acme.test")
    mallory = await make_user(db, acme, "mallory@acme.test")

    project_a = await make_project(db, acme, alice, name="A")
    project_b = await make_project(db, acme, alice, name="B")
    await make_grant(
        db, org_id=acme.id, subject_type="project", subject_id=project_a.id,
        user=mallory, role="viewer",
    )
    # mallory has no access at all to project_b.

    document = await make_document(db, alice, project_b, title="Confidential")
    await make_grant(
        db, org_id=acme.id, subject_type="document", subject_id=document.id,
        user=alice, role="owner",
    )
    await db.commit()

    # Indexed as though it were still filed under project A — a stale
    # payload, exactly as a move without a reindex would leave behind.
    await store.index_document(
        document_id=document.id, org_id=acme.id, project_id=project_a.id,
        owner_id=alice.id, title="Confidential", pages=[Page(page=1, text=SECRET)],
    )

    scope = await _scope(db, mallory)
    assert await store.search_documents(db=db, scope=scope, query=SECRET, limit=12) == []


async def test_point_missing_tenancy_or_source_is_never_returned(db, rag):
    acme = await make_org(db, "acme")
    alice = await make_user(db, acme, "alice@acme.test")
    project = await make_project(db, acme, alice)
    await make_grant(
        db, org_id=acme.id, subject_type="project", subject_id=project.id,
        user=alice, role="owner",
    )
    document = await make_document(db, alice, project, title="Leaky")
    await make_grant(
        db, org_id=acme.id, subject_type="document", subject_id=document.id,
        user=alice, role="owner",
    )
    await db.commit()
    scope = await _scope(db, alice)
    await store.ensure_collection()

    no_tenancy_text = "A chunk written by code that forgot the tenancy payload."
    await store.client().upsert(
        collection_name=settings.qdrant_collection,
        points=[
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=_vec(no_tenancy_text),
                payload={
                    "owner_id": str(alice.id),
                    "document_id": str(document.id),
                    "title": "Leaky",
                    "chunk": 1,
                    "text": no_tenancy_text,
                    # no org_id, no source
                },
            )
        ],
        wait=True,
    )
    assert await store.search_documents(db=db, scope=scope, query=no_tenancy_text, limit=12) == []

    no_source_text = "Org and owner are right, but nothing marks this as a document."
    await store.client().upsert(
        collection_name=settings.qdrant_collection,
        points=[
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=_vec(no_source_text),
                payload={
                    "org_id": str(acme.id),
                    "owner_id": str(alice.id),
                    "document_id": str(document.id),
                    "title": "Leaky",
                    "chunk": 2,
                    "text": no_source_text,
                    # no "source": "document"
                },
            )
        ],
        wait=True,
    )
    assert await store.search_documents(db=db, scope=scope, query=no_source_text, limit=12) == []


def test_document_admits_matches_the_file_admits_shape():
    """Belt-and-braces re-check, exercised directly — the same property
    test_rag_isolation.py asserts of `store._admits` for files."""
    org, user, project = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scope = RetrievalScope(org_id=org, user_id=user, project_ids=(project,))

    ok_owner = {"source": "document", "org_id": str(org), "owner_id": str(user), "project_id": ""}
    ok_project = {
        "source": "document", "org_id": str(org),
        "owner_id": str(uuid.uuid4()), "project_id": str(project),
    }
    wrong_org = {
        "source": "document", "org_id": str(uuid.uuid4()),
        "owner_id": str(user), "project_id": str(project),
    }
    not_a_document = {"org_id": str(org), "owner_id": str(user), "project_id": ""}

    assert store._document_admits(scope, ok_owner)
    assert store._document_admits(scope, ok_project)
    assert not store._document_admits(scope, wrong_org)
    assert not store._document_admits(scope, not_a_document)
    assert not store._document_admits(scope, None)
    assert not store._document_admits(RetrievalScope(org_id=None, user_id=user), ok_owner)


# ────────────────────────────────── backfill ────────────────────────────────


async def test_backfill_documents_indexes_every_row(db, rag, session_factory, monkeypatch):
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    first = await make_document(db, user, project, title="One")
    first.content_json = _doc(_p("First document body."))
    second = await make_document(db, user, project, title="Two")
    second.content_json = _doc(_p("Second document body."))
    await db.commit()

    monkeypatch.setattr(backfill_documents_module, "SessionLocal", session_factory)
    seen = await backfill_documents_module.backfill()
    assert seen == 2

    first_points = await _document_points(first.id)
    second_points = await _document_points(second.id)
    assert len(first_points) == 1 and "First document body." in first_points[0].payload["text"]
    assert len(second_points) == 1 and "Second document body." in second_points[0].payload["text"]


async def test_backfill_documents_dry_run_writes_nothing(db, rag, session_factory, monkeypatch):
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="One")
    document.content_json = _doc(_p("Body."))
    await db.commit()

    monkeypatch.setattr(backfill_documents_module, "SessionLocal", session_factory)
    seen = await backfill_documents_module.backfill(dry_run=True)
    assert seen == 1
    assert await _document_points(document.id) == []


async def test_backfill_documents_is_idempotent(db, rag, session_factory, monkeypatch):
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="One")
    document.content_json = _doc(_p("Stable body."))
    await db.commit()

    monkeypatch.setattr(backfill_documents_module, "SessionLocal", session_factory)
    await backfill_documents_module.backfill()
    ids_first = {p.id for p in await _document_points(document.id)}

    await backfill_documents_module.backfill()
    ids_second = {p.id for p in await _document_points(document.id)}
    assert ids_first == ids_second and len(ids_first) == 1


async def test_backfill_documents_reads_through_the_privileged_connection_when_configured(
    db, rag, migrated_dsn, monkeypatch
):
    """Exercises the settings.alembic_database_url branch end-to-end, not
    just the SessionLocal fallback the other backfill tests use."""
    org = await make_org(db, "acme")
    user = await make_user(db, org, "alice@acme.test")
    project = await make_project(db, org, user)
    document = await make_document(db, user, project, title="Privileged")
    document.content_json = _doc(_p("Read through the privileged connection."))
    await db.commit()

    monkeypatch.setattr(settings, "alembic_database_url", migrated_dsn)
    seen = await backfill_documents_module.backfill()
    assert seen >= 1
    points = await _document_points(document.id)
    assert len(points) == 1


async def test_backfill_documents_sees_every_org_even_though_the_app_role_alone_sees_none(
    doc_world, app_session_factory, migrated_dsn, monkeypatch
):
    """The same regression proven for rag/backfill.py in test_rag_isolation.py,
    proven here for rag/backfill_documents.py.

    `SessionLocal` is monkeypatched to the unprivileged, unbound app role —
    exactly what it resolves to in a real deployment, and exactly what
    `backfill()` used unconditionally before this fix. That makes this a
    real tripwire: if the privileged-connection branch were ever removed,
    `backfill()` would fall through to this same deny-all session and read
    zero rows — this test would then fail on the row count below.
    """
    monkeypatch.setattr(backfill_documents_module, "SessionLocal", app_session_factory)

    async with anonymous(app_session_factory) as blind:
        assert (await blind.execute(select(Document))).scalars().all() == []

    monkeypatch.setattr(settings, "alembic_database_url", migrated_dsn)
    seen = await backfill_documents_module.backfill(dry_run=True)
    assert seen == 2  # p_doc (acme) and g_doc (globex)
