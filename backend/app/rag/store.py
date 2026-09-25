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

from app.config import settings
from app.rag.loaders import Page
from app.rag.scope import RetrievalScope

logger = logging.getLogger(__name__)

_NAMESPACE = uuid.UUID("6f3ac0de-0000-4000-8000-000000000001")

# Payload fields every point carries. org_id / project_id / owner_id are the
# tenancy triple the filter pins; file_id is the relational anchor.
_KEYWORD_FIELDS = (
    "org_id",
    "project_id",
    "owner_id",
    "file_id",
    "session_id",
    "comparison_role",
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
