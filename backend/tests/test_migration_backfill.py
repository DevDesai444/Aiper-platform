"""Migration 0002's backfill, driven through the real migrations.

The backfill only ever runs once against real data, so this is the only chance
to find out whether it is right. The test stands up a database at 0001, fills it
with rows shaped like the ones that exist today, upgrades it, and checks what
came out the other side.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import alembic_upgrade

LEGACY_ROWS = """
INSERT INTO users (id, email, full_name, organisation, hashed_password, is_active,
                   created_at, updated_at) VALUES
  ('11111111-1111-1111-1111-111111111111','a@acme.example','A','Acme Inc','x',true,now(),now()),
  ('22222222-2222-2222-2222-222222222222','b@acme.example','B','Acme, Inc.','x',true,now(),now()),
  ('33333333-3333-3333-3333-333333333333','c@orbital.example','C','Orbital Systems','x',true,now(),now()),
  ('44444444-4444-4444-4444-444444444444','d@none.example','D','','x',true,now(),now()),
  ('55555555-5555-5555-5555-555555555555','e@junk.example','E','!!!','x',true,now(),now());

INSERT INTO documents (id, owner_id, title, content_json, content_text, revision_count,
                       created_at, updated_at) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','A one','{}','',0,now(),now()),
  ('aaaaaaaa-0000-0000-0000-000000000002','11111111-1111-1111-1111-111111111111','A two','{}','',0,now(),now()),
  ('bbbbbbbb-0000-0000-0000-000000000001','33333333-3333-3333-3333-333333333333','C one','{}','',0,now(),now());

-- One share inside the organisation, one that crosses it.
INSERT INTO document_collaborators (id, document_id, email, user_id, role, invite_status,
                                    created_at, updated_at) VALUES
  ('cccccccc-0000-0000-0000-000000000001','aaaaaaaa-0000-0000-0000-000000000001','b@acme.example','22222222-2222-2222-2222-222222222222','editor','accepted',now(),now()),
  ('cccccccc-0000-0000-0000-000000000002','aaaaaaaa-0000-0000-0000-000000000001','c@orbital.example','33333333-3333-3333-3333-333333333333','viewer','accepted',now(),now()),
  ('cccccccc-0000-0000-0000-000000000003','aaaaaaaa-0000-0000-0000-000000000002','nobody@acme.example',NULL,'editor','pending',now(),now());

INSERT INTO chat_sessions (id, owner_id, title, mode, created_at, updated_at) VALUES
  ('dddddddd-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','S','document_generation',now(),now());

INSERT INTO file_assets (id, owner_id, filename, content_type, extension, size_bytes,
                         storage_path, page_count, indexed, comparison_role,
                         created_at, updated_at) VALUES
  ('eeeeeeee-0000-0000-0000-000000000001','33333333-3333-3333-3333-333333333333','f.pdf','application/pdf','pdf',1,'/x',1,true,'source',now(),now());
"""

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
CAROL = "33333333-3333-3333-3333-333333333333"
ALICE_DOC = "aaaaaaaa-0000-0000-0000-000000000001"
ALICE_DOC_2 = "aaaaaaaa-0000-0000-0000-000000000002"


@pytest.fixture
def legacy_dsn(scratch_database) -> str:
    """A database at revision 0001, ready to be filled with legacy rows.

    Synchronous, because creating a database and shelling out to alembic must
    happen outside the event loop the test itself runs on.
    """
    dsn = scratch_database("aiper_backfill")
    alembic_upgrade(dsn, "0001")
    return dsn


@pytest.fixture
async def backfilled(legacy_dsn: str):
    """Legacy rows at 0001, then upgraded to head."""
    dsn = legacy_dsn
    engine = create_async_engine(dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for statement in filter(None, (s.strip() for s in LEGACY_ROWS.split(";"))):
                await session.execute(text(statement))
            await session.commit()

        alembic_upgrade(dsn)

        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def test_users_are_grouped_into_organisations_by_slug(backfilled):
    rows = (
        await backfilled.execute(
            text("SELECT name, slug FROM organisations ORDER BY slug")
        )
    ).all()
    assert [r.slug for r in rows] == ["acme-inc", "default", "orbital-systems"]

    pairs = (
        await backfilled.execute(
            text(
                "SELECT u.email, o.slug FROM users u"
                " JOIN organisations o ON o.id = u.org_id ORDER BY u.email"
            )
        )
    ).all()
    assert dict(pairs) == {
        # The two spellings of Acme are one organisation.
        "a@acme.example": "acme-inc",
        "b@acme.example": "acme-inc",
        "c@orbital.example": "orbital-systems",
        # A blank name and an unsluggable one both land in the catch-all.
        "d@none.example": "default",
        "e@junk.example": "default",
    }


async def test_every_document_lands_in_its_owner_s_workspace(backfilled):
    rows = (
        await backfilled.execute(
            text(
                "SELECT d.title, p.name AS project, p.created_by, o.slug, d.folder_id"
                " FROM documents d"
                " JOIN projects p ON p.id = d.project_id"
                " JOIN organisations o ON o.id = d.org_id"
                " ORDER BY d.title"
            )
        )
    ).all()
    assert [r.project for r in rows] == ["Workspace"] * 3
    assert [r.folder_id for r in rows] == [None, None, None]

    by_title = {r.title: r for r in rows}
    assert str(by_title["A one"].created_by) == ALICE
    assert str(by_title["A two"].created_by) == ALICE
    assert str(by_title["C one"].created_by) == CAROL
    assert by_title["A one"].slug == "acme-inc"
    assert by_title["C one"].slug == "orbital-systems"

    # One workspace per owner, and only for owners who had documents.
    projects = (
        await backfilled.execute(text("SELECT count(*) FROM projects"))
    ).scalar_one()
    assert projects == 2


async def test_owners_keep_owner_access_and_in_org_shares_survive(backfilled):
    grants = (
        await backfilled.execute(
            text(
                "SELECT g.subject_type::text, g.role::text, u.email, g.subject_id"
                " FROM access_grants g JOIN users u ON u.id = g.user_id"
            )
        )
    ).all()

    document_grants = {
        (row[2], str(row[3])): row[1] for row in grants if row[0] == "document"
    }
    assert document_grants[("a@acme.example", ALICE_DOC)] == "owner"
    assert document_grants[("a@acme.example", ALICE_DOC_2)] == "owner"
    # The in-organisation share became an editor grant.
    assert document_grants[("b@acme.example", ALICE_DOC)] == "editor"
    # The share that crossed organisations did not.
    assert ("c@orbital.example", ALICE_DOC) not in document_grants

    project_grants = [row for row in grants if row[0] == "project"]
    assert {row[2] for row in project_grants} == {"a@acme.example", "c@orbital.example"}
    assert {row[1] for row in project_grants} == {"owner"}


async def test_the_resolver_agrees_with_the_backfill(backfilled):
    async def access(user: str, subject_type: str, subject_id: str) -> str | None:
        return (
            await backfilled.execute(
                text(
                    "SELECT aiper_effective_access(CAST(:u AS uuid),"
                    " CAST(:t AS aiper_subject), CAST(:s AS uuid))"
                ),
                {"u": user, "t": subject_type, "s": subject_id},
            )
        ).scalar_one()

    assert await access(ALICE, "document", ALICE_DOC) == "owner"
    assert await access(BOB, "document", ALICE_DOC) == "editor"
    # Bob's share was on one document only; the other stays out of reach.
    assert await access(BOB, "document", ALICE_DOC_2) is None
    # Carol is in another organisation.
    assert await access(CAROL, "document", ALICE_DOC) is None


async def test_the_legacy_organisation_string_is_kept(backfilled):
    rows = (
        await backfilled.execute(
            text("SELECT organisation FROM users WHERE email = 'b@acme.example'")
        )
    ).scalar_one()
    assert rows == "Acme, Inc.", "the free-text column is still needed by /auth/me"
