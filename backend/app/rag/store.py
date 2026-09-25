"""Qdrant page index. One point is one page.

Isolation is enforced here, in the store, rather than by prompt instruction.
Every query is built through `_filter()`, which always pins the caller's
`RetrievalScope`: the organisation as an outer must-condition, and inside it
only pages the caller owns or pages in a project the access resolver admits
them to. A point that lacks the tenancy payload matches neither branch, so
unmigrated or hand-crafted points are invisible — fail closed, not open.

Every hit is then re-verified against the same scope before it is returned
(`_admits`). The filter makes a leak impossible; the re-check makes a bug in
the filter loud instead of silent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.rag.loaders import Page
from app.rag.scope import RetrievalScope, admitted_document_ids

logger = logging.getLogger(__name__)

_NAMESPACE = uuid.UUID("6f3ac0de-0000-4000-8000-000000000001")

# Payload fields every point carries. org_id / project_id / owner_id are the
# tenancy triple the filter pins; file_id is the relational anchor.
#
# document_id and source (E5, document corpus) are additive: file points
# never carry them, so a filter on either only ever matches document points.
_KEYWORD_FIELDS = (
    "org_id",
    "project_id",
    "owner_id",
    "file_id",
    "session_id",
    "comparison_role",
    "document_id",
    "source",
)


@dataclass(slots=True)
class Hit:
    file_id: str
    filename: str
    page: int
    text: str
    score: float

    def citation(self) -> str:
        return f"[{self.filename}, p.{self.page}]"


_client: AsyncQdrantClient | None = None
_ensured = False


def client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    return _client


def _embeddings():
    from langchain_openai import AzureOpenAIEmbeddings

    return AzureOpenAIEmbeddings(
        azure_deployment=settings.azure_openai_embedding_deployment_name,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )


async def ensure_collection() -> None:
    """Create the collection if missing, and ensure the payload indexes exist.

    The index step runs even when the collection already exists, so an install
    upgraded in place gains the tenancy indexes without a recreate. Both calls
    are idempotent; the guard just keeps it to once per process.
    """
    global _ensured
    if _ensured:
        return

    c = client()
    existing = {col.name for col in (await c.get_collections()).collections}
    if settings.qdrant_collection not in existing:
        await c.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=models.VectorParams(
                size=settings.embedding_dim, distance=models.Distance.COSINE
            ),
        )
    for field in _KEYWORD_FIELDS:
        await c.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    await c.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="page",
        field_schema=models.PayloadSchemaType.INTEGER,
    )
    _ensured = True


async def index_pages(
    *,
    owner_id: uuid.UUID,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    file_id: uuid.UUID,
    filename: str,
    session_id: uuid.UUID | None,
    comparison_role: str,
    pages: list[Page],
) -> int:
    if not pages:
        return 0

    await ensure_collection()
    vectors = await _embeddings().aembed_documents([p.text for p in pages])

    points = [
        models.PointStruct(
            # uuid5 over (file, page) keeps re-indexing idempotent.
            id=str(uuid.uuid5(_NAMESPACE, f"{file_id}:{page.page}")),
            vector=vector,
            payload={
                "org_id": str(org_id),
                "project_id": str(project_id) if project_id else "",
                "owner_id": str(owner_id),
                "file_id": str(file_id),
                "filename": filename,
                "session_id": str(session_id) if session_id else "",
                "comparison_role": comparison_role,
                "page": page.page,
                "text": page.text,
            },
        )
        for page, vector in zip(pages, vectors, strict=True)
    ]
    await client().upsert(collection_name=settings.qdrant_collection, points=points, wait=True)
    return len(points)


def _filter(
    scope: RetrievalScope,
    *,
    file_ids: list[uuid.UUID] | None = None,
    comparison_role: str | None = None,
) -> models.Filter:
    """AND(org, OR(own, admitted project), [narrowers]).

    The org pin and the owner/project disjunction come from the scope alone;
    `file_ids` and `comparison_role` can only narrow further, never widen.
    A scope without an organisation matches nothing.
    """
    must: list[models.Condition] = [
        models.FieldCondition(key="org_id", match=models.MatchValue(value=str(scope.org_id))),
        models.Filter(
            should=[
                models.FieldCondition(
                    key="owner_id", match=models.MatchValue(value=str(scope.user_id))
                ),
                *(
                    [
                        models.FieldCondition(
                            key="project_id",
                            match=models.MatchAny(any=[str(p) for p in scope.project_ids]),
                        )
                    ]
                    if scope.project_ids
                    else []
                ),
            ]
        ),
    ]
    if file_ids:
        must.append(
            models.FieldCondition(
                key="file_id", match=models.MatchAny(any=[str(f) for f in file_ids])
            )
        )
    if comparison_role:
        must.append(
            models.FieldCondition(
                key="comparison_role", match=models.MatchValue(value=comparison_role)
            )
        )
    return models.Filter(must=must)


def _admits(scope: RetrievalScope, payload: dict | None) -> bool:
    """The scope rule, re-applied to a returned payload.

    Belt and braces over `_filter`: Qdrant already enforced this server-side,
    so a point failing here means the filter and the payload have drifted —
    it is dropped and logged as an error, never returned.
    """
    if not payload or scope.org_id is None:
        return False
    if payload.get("org_id") != str(scope.org_id):
        return False
    if payload.get("owner_id") == str(scope.user_id):
        return True
    project = payload.get("project_id") or ""
    return bool(project) and project in {str(p) for p in scope.project_ids}


def _hits(scope: RetrievalScope, points) -> list[Hit]:
    hits: list[Hit] = []
    for p in points:
        if not _admits(scope, p.payload):
            logger.error(
                "Retrieval filter drift: point %s escaped the scope filter "
                "(org=%s user=%s) and was dropped",
                p.id,
                scope.org_id,
                scope.user_id,
            )
            continue
        hits.append(
            Hit(
                file_id=str(p.payload.get("file_id", "")),
                filename=str(p.payload.get("filename", "")),
                page=int(p.payload.get("page", 0)),
                text=str(p.payload.get("text", "")),
                score=float(getattr(p, "score", None) or 0.0),
            )
        )
    return hits


async def search(
    *,
    scope: RetrievalScope,
    query: str,
    limit: int = 6,
    file_ids: list[uuid.UUID] | None = None,
    comparison_role: str | None = None,
) -> list[Hit]:
    await ensure_collection()
    vector = await _embeddings().aembed_query(query)
    result = await client().query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        query_filter=_filter(scope, file_ids=file_ids, comparison_role=comparison_role),
        limit=limit,
        with_payload=True,
    )
    return _hits(scope, result.points)


async def read_file_pages(*, scope: RetrievalScope, file_id: uuid.UUID) -> list[Hit]:
    """Every page of one file, in order — used to read a comparison target."""
    await ensure_collection()
    points, _ = await client().scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=_filter(scope, file_ids=[file_id]),
        limit=1000,
        with_payload=True,
    )
    return sorted(_hits(scope, points), key=lambda h: h.page)


async def delete_file(*, org_id: uuid.UUID, owner_id: uuid.UUID, file_id: uuid.UUID) -> None:
    """Remove one file's pages. Pinned to org and owner: deletion stays the
    uploader's own act even if the route in front of it ever forgets to check."""
    await ensure_collection()
    await client().delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="org_id", match=models.MatchValue(value=str(org_id))
                    ),
                    models.FieldCondition(
                        key="owner_id", match=models.MatchValue(value=str(owner_id))
                    ),
                    models.FieldCondition(
                        key="file_id", match=models.MatchValue(value=str(file_id))
                    ),
                ]
            )
        ),
    )


async def set_file_tenancy(
    *, file_id: uuid.UUID, org_id: uuid.UUID, project_id: uuid.UUID | None
) -> None:
    """Stamp one file's points with its current tenancy row.

    The write path for keeping payload and database in agreement: the backfill
    uses it to migrate pre-tenancy points, and anything that later moves a file
    between projects must call it in the same operation.
    """
    await ensure_collection()
    await client().set_payload(
        collection_name=settings.qdrant_collection,
        payload={
            "org_id": str(org_id),
            "project_id": str(project_id) if project_id else "",
        },
        points=models.Filter(
            must=[
                models.FieldCondition(key="file_id", match=models.MatchValue(value=str(file_id)))
            ]
        ),
    )


# ════════════════════════════ document corpus (E5) ═════════════════════════
#
# What the platform itself produces, indexed the same way an upload is: one
# collection, one point per chunk. Everything below is additive — no file
# function above is touched, and a document point carries "source": "document"
# plus "document_id", two keys no file point has, so the two families can
# never collide in a filter that pins on either.
#
# The read side is stricter than a file's, and deliberately not folded into
# `search()`/`_filter()`/`_admits()` above. A file's true access is exactly
# "owner, or a project the resolver admits" — the same rule `RetrievalScope`
# already encodes. A document's true access can *also* come from a grant on
# one specific document, or on a folder somewhere above it, neither of which
# is visible from `RetrievalScope.project_ids` (project-level admission
# alone). So the Qdrant filter here can only be a coarse candidate set, in
# the same owner-or-admitted-project shape as a file's — and every candidate
# it returns is re-verified in one SQL round trip against
# `aiper_effective_access` (see `app.rag.scope.admitted_document_ids`) before
# it is returned. That re-check is what actually enforces the boundary; the
# coarse filter only bounds how much work Qdrant does before it.
#
# The coarse filter is therefore both over- and under-inclusive relative to
# the SQL truth: over-inclusive because "admitted project" says nothing about
# a narrower folder- or document-level restriction the SQL check then
# catches; under-inclusive because a user who reaches a document *only*
# through a folder or per-document grant — never through project-level
# access — never has that document's project in `scope.project_ids` at all,
# so its points do not even reach the coarse filter. That under-inclusion
# (a folder-granted document in a project the user has no *project-level*
# grant on) is a known, accepted gap for this unit, not a bug: closing it
# means growing `RetrievalScope` itself to carry folder/document admission,
# which is real, separate work for whoever wires document retrieval into the
# agent next.


_DOCUMENT_NAMESPACE_PREFIX = "document"


def _document_filter(
    scope: RetrievalScope, *, document_ids: list[uuid.UUID] | None = None
) -> models.Filter:
    """The coarse candidate-set gate for document points — see the section
    docstring above for why this cannot be the final answer on its own."""
    must: list[models.Condition] = [
        models.FieldCondition(key="org_id", match=models.MatchValue(value=str(scope.org_id))),
        models.FieldCondition(key="source", match=models.MatchValue(value="document")),
        models.Filter(
            should=[
                models.FieldCondition(
                    key="owner_id", match=models.MatchValue(value=str(scope.user_id))
                ),
                *(
                    [
                        models.FieldCondition(
                            key="project_id",
                            match=models.MatchAny(any=[str(p) for p in scope.project_ids]),
                        )
                    ]
                    if scope.project_ids
                    else []
                ),
            ]
        ),
    ]
    if document_ids:
        must.append(
            models.FieldCondition(
                key="document_id", match=models.MatchAny(any=[str(d) for d in document_ids])
            )
        )
    return models.Filter(must=must)


def _document_admits(scope: RetrievalScope, payload: dict | None) -> bool:
    """Belt-and-braces re-check of the coarse filter, exactly as `_admits` is
    for files. This is not the document access answer — `admitted_document_ids`
    is — it only catches the filter and the payload having drifted apart."""
    if not payload or scope.org_id is None:
        return False
    if payload.get("source") != "document":
        return False
    if payload.get("org_id") != str(scope.org_id):
        return False
    if payload.get("owner_id") == str(scope.user_id):
        return True
    project = payload.get("project_id") or ""
    return bool(project) and project in {str(p) for p in scope.project_ids}


def _document_hits(scope: RetrievalScope, points) -> list[Hit]:
    """`Hit` is reused as-is for a document chunk: nothing in its shape is
    file-specific, `file_id` holds the document's id, and `page` holds the
    chunk number — so a citation reads `[Title, p.N]` for either kind, and
    anything already written against `list[Hit]` keeps working unchanged."""
    hits: list[Hit] = []
    for p in points:
        if not _document_admits(scope, p.payload):
            logger.error(
                "Document retrieval filter drift: point %s escaped the coarse "
                "filter (org=%s user=%s) and was dropped",
                p.id,
                scope.org_id,
                scope.user_id,
            )
            continue
        hits.append(
            Hit(
                file_id=str(p.payload.get("document_id", "")),
                filename=str(p.payload.get("title", "")),
                page=int(p.payload.get("chunk", 0)),
                text=str(p.payload.get("text", "")),
                score=float(getattr(p, "score", None) or 0.0),
            )
        )
    return hits


async def index_document(
    *,
    document_id: uuid.UUID,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    title: str,
    pages: list[Page],
) -> int:
    """Replace one document's whole point set: delete, then upsert.

    Upserting alone (even with deterministic ids) is not enough to keep the
    index correct — it only ever overwrites chunk numbers the new content
    still has. A commit that makes a document *shorter* would leave the
    tail's old chunks behind, stale and still fully citable, forever. Delete
    first and a commit's index always matches its content exactly, with
    nothing left over from a longer earlier revision.

    Point ids are uuid5("document:{document_id}:{chunk}") — the same
    idempotent-id idiom `index_pages` uses for files, with a type prefix so a
    document id and a file id can never coincidentally hash to the same
    point even in the astronomical case they collide as strings.
    """
    await ensure_collection()
    await delete_document_points(org_id=org_id, document_id=document_id)
    if not pages:
        return 0

    vectors = await _embeddings().aembed_documents([p.text for p in pages])
    points = [
        models.PointStruct(
            id=str(
                uuid.uuid5(
                    _NAMESPACE, f"{_DOCUMENT_NAMESPACE_PREFIX}:{document_id}:{page.page}"
                )
            ),
            vector=vector,
            payload={
                "org_id": str(org_id),
                "project_id": str(project_id) if project_id else "",
                "owner_id": str(owner_id),
                "document_id": str(document_id),
                "title": title,
                "chunk": page.page,
                "text": page.text,
                "source": "document",
            },
        )
        for page, vector in zip(pages, vectors, strict=True)
    ]
    await client().upsert(collection_name=settings.qdrant_collection, points=points, wait=True)
    return len(points)


async def delete_document_points(*, org_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Remove every point for one document.

    Called by `index_document` before every re-index. Not yet wired into the
    document-delete route — `documents.py` is contested by open work and this
    unit keeps its edit there to the one commit-time call — so a deleted
    document's points currently outlive the row; exposed here so that wiring
    is a one-line follow-up once the file is free to touch again.
    """
    await ensure_collection()
    await client().delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="org_id", match=models.MatchValue(value=str(org_id))
                    ),
                    models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=str(document_id))
                    ),
                    models.FieldCondition(key="source", match=models.MatchValue(value="document")),
                ]
            )
        ),
    )


async def search_documents(
    *,
    db: AsyncSession,
    scope: RetrievalScope,
    query: str,
    limit: int = 6,
    document_ids: list[uuid.UUID] | None = None,
) -> list[Hit]:
    """Search document chunks: a coarse Qdrant candidate set, narrowed to the
    SQL truth in one round trip. See the section docstring above.

    Fetches exactly `limit` candidates and drops whatever the resolver does
    not admit — it does not re-query to top back up to `limit`, so a result
    set can legitimately come back shorter when the coarse filter's
    over-inclusion turns out to matter for this particular query.
    """
    if scope.org_id is None:
        return []
    await ensure_collection()
    vector = await _embeddings().aembed_query(query)
    result = await client().query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        query_filter=_document_filter(scope, document_ids=document_ids),
        limit=limit,
        with_payload=True,
    )
    candidates = _document_hits(scope, result.points)
    if not candidates:
        return []

    allowed = await admitted_document_ids(
        db,
        user_id=scope.user_id,
        document_ids=[uuid.UUID(c.file_id) for c in candidates],
    )
    return [c for c in candidates if uuid.UUID(c.file_id) in allowed]
