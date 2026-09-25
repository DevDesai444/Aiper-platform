"""Index every existing document's current content into Qdrant.

    python -m app.rag.backfill_documents [--dry-run]

A document committed before this unit shipped has never been chunked or
indexed — there is no Qdrant point for it until this runs once. Walks every
`documents` row and reindexes it through the exact path a live commit uses
(`app.rag.corpus.chunk_document` + `app.rag.store.index_document`), so the
result is identical to committing every document again, without writing a
new revision or touching `content_text`/`revision_count`.

Idempotent and safe while the app is serving: `index_document` deletes a
document's points before writing its new ones, so re-running is a no-op for
any document whose content has not changed since the last run. Run with
--dry-run to see the row count without writing.

Reads through the privileged connection (`ALEMBIC_DATABASE_URL`, the same
role migrations and boot-time template seeding use — see
`app.services.seed`), not the app's request-scoped session: every table is
FORCE ROW LEVEL SECURITY as of migration 0003, and a system job walking every
organisation's documents at once has no single identity to bind. Falls back
to the plain session for a deployment that still runs a single privileged
database role.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import Document
from app.rag import store
from app.rag.corpus import chunk_document

logger = logging.getLogger(__name__)

_PROGRESS_EVERY = 50


async def _rows(db: AsyncSession) -> list:
    return (
        await db.execute(
            select(
                Document.id,
                Document.org_id,
                Document.project_id,
                Document.owner_id,
                Document.title,
                Document.content_json,
            ).order_by(Document.created_at)
        )
    ).all()


async def backfill(*, dry_run: bool = False) -> int:
    """Reindex every document's current content. Returns rows seen."""
    if settings.alembic_database_url:
        engine = create_async_engine(settings.alembic_database_url, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as db:
                rows = await _rows(db)
        finally:
            await engine.dispose()
    else:
        async with SessionLocal() as db:
            rows = await _rows(db)

    for count, (document_id, org_id, project_id, owner_id, title, content_json) in enumerate(
        rows, start=1
    ):
        if not dry_run:
            try:
                pages = chunk_document(content_json)
                await store.index_document(
                    document_id=document_id,
                    org_id=org_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    title=title,
                    pages=pages,
                )
            except Exception:  # noqa: BLE001 - one bad document must not stop the run
                logger.error(
                    "Backfill: indexing failed for document %s", document_id, exc_info=True
                )
        if count % _PROGRESS_EVERY == 0:
            logger.info("Backfill: %d/%d documents indexed", count, len(rows))

    logger.info(
        "Backfill %s: %d document rows %s",
        "dry-run" if dry_run else "done",
        len(rows),
        "would be indexed" if dry_run else "indexed",
    )
    return len(rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="count the rows, write nothing"
    )
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
