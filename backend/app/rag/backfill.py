"""Stamp tenancy payload onto Qdrant points that predate it.

    python -m app.rag.backfill [--dry-run]

Points indexed before the tenancy filter carry no org_id / project_id, so the
filter refuses them — fail closed. This walks every file_assets row and writes
that row's org_id and project_id onto the file's points, after which they are
visible again to exactly the people the access resolver admits.

Cutover order is therefore: migrate the database (alembic 0002 gives every
file row an org), deploy the enforcing code, run this. Enforcement never
waits on the backfill; the backfill only restores visibility. Idempotent —
re-running rewrites the same values — and safe while the app is serving.

Points whose file row no longer exists are left untouched: with no org_id
they stay unreachable, and the file delete path is the one that removes
points. Run with --dry-run to see the row count without writing.

Reads through the privileged connection (`ALEMBIC_DATABASE_URL`), not the
app's request-scoped session: every table is FORCE ROW LEVEL SECURITY as of
migration 0003, and this walks every organisation's files at once, which has
no single identity to bind a normal session to — see `app.services.seed` for
the same pattern applied to boot-time template seeding. Falls back to the
plain session for a deployment that still runs a single privileged role.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import FileAsset
from app.rag import store

logger = logging.getLogger(__name__)

_PROGRESS_EVERY = 100


async def backfill(*, dry_run: bool = False) -> int:
    """Stamp every file's points with its row's tenancy. Returns rows seen."""
    query = select(FileAsset.id, FileAsset.org_id, FileAsset.project_id).order_by(
        FileAsset.created_at
    )
    if settings.alembic_database_url:
        engine = create_async_engine(settings.alembic_database_url, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as db:
                rows = (await db.execute(query)).all()
        finally:
            await engine.dispose()
    else:
        async with SessionLocal() as db:
            rows = (await db.execute(query)).all()

    for count, (file_id, org_id, project_id) in enumerate(rows, start=1):
        if not dry_run:
            await store.set_file_tenancy(
                file_id=file_id, org_id=org_id, project_id=project_id
            )
        if count % _PROGRESS_EVERY == 0:
            logger.info("Backfill: %d/%d files stamped", count, len(rows))

    logger.info(
        "Backfill %s: %d file rows %s",
        "dry-run" if dry_run else "done",
        len(rows),
        "would be stamped" if dry_run else "stamped",
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
