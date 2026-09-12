"""Qdrant page index. One point is one page.

Every query is built through `_filter()`, which always pins `owner_id`, so a
tool physically cannot reach another user's pages — isolation is enforced here
rather than by prompt instruction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.config import settings
from app.rag.loaders import Page

_NAMESPACE = uuid.UUID("6f3ac0de-0000-4000-8000-000000000001")


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
    c = client()
    existing = {col.name for col in (await c.get_collections()).collections}
    if settings.qdrant_collection in existing:
        return

    await c.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=models.VectorParams(
            size=settings.embedding_dim, distance=models.Distance.COSINE
        ),
    )
    for field in ("owner_id", "file_id", "session_id", "comparison_role"):
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


async def index_pages(
    *,
    owner_id: uuid.UUID,
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
    *,
    owner_id: uuid.UUID,
    file_ids: list[uuid.UUID] | None = None,
    comparison_role: str | None = None,
) -> models.Filter:
    must: list[models.Condition] = [
        models.FieldCondition(key="owner_id", match=models.MatchValue(value=str(owner_id)))
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


async def search(
    *,
    owner_id: uuid.UUID,
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
        query_filter=_filter(
            owner_id=owner_id, file_ids=file_ids, comparison_role=comparison_role
        ),
        limit=limit,
        with_payload=True,
    )
    return [
        Hit(
            file_id=str(p.payload.get("file_id", "")),
            filename=str(p.payload.get("filename", "")),
            page=int(p.payload.get("page", 0)),
            text=str(p.payload.get("text", "")),
            score=float(p.score or 0.0),
        )
        for p in result.points
    ]


async def read_file_pages(*, owner_id: uuid.UUID, file_id: uuid.UUID) -> list[Hit]:
    """Every page of one file, in order — used to read a comparison target."""
    await ensure_collection()
    points, _ = await client().scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=_filter(owner_id=owner_id, file_ids=[file_id]),
        limit=1000,
        with_payload=True,
    )
    hits = [
        Hit(
            file_id=str(p.payload.get("file_id", "")),
            filename=str(p.payload.get("filename", "")),
            page=int(p.payload.get("page", 0)),
            text=str(p.payload.get("text", "")),
            score=1.0,
        )
        for p in points
    ]
    return sorted(hits, key=lambda h: h.page)


async def delete_file(*, owner_id: uuid.UUID, file_id: uuid.UUID) -> None:
    await ensure_collection()
    await client().delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=_filter(owner_id=owner_id, file_ids=[file_id])
        ),
    )
