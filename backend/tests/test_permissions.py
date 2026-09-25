"""The access resolver, and the routes that depend on it.

These are the tests the rest of the isolation work is built on: row-level
security, retrieval filtering and UI gating all key off the same function, so a
hole here is a hole everywhere.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.services.permissions import effective_access, grant_access, revoke_access
from tests.factories import (
    make_document,
    make_folder,
    make_grant,
    make_org,
    make_project,
    make_user,
)

# ─────────────────────────── the organisation boundary ─────────────────────


async def test_grant_across_organisations_is_inert(db):
    """The boundary is absolute: a forged grant buys nothing.

    This is the property everything else leans on, so it is checked against a
    row the application would never write — inserted straight into the table,
    with the attacker's own organisation on it so that no denormalised column
    gives the game away.
    """
    org_a = await make_org(db, "org-a")
    org_b = await make_org(db, "org-b")
    alice = await make_user(db, org_a, "alice@a.example")
    mallory = await make_user(db, org_b, "mallory@b.example")

    project = await make_project(db, org_a, alice)
    document = await make_document(db, alice, project)

    await make_grant(
        db,
        org_id=org_b.id,  # the attacker's own organisation
        subject_type="document",
        subject_id=document.id,
        user=mallory,
        role="owner",
    )

    assert await effective_access(db, alice.id, "document", document.id) is None, (
        "alice has no grant of her own yet"
    )
    assert await effective_access(db, mallory.id, "document", document.id) is None


async def test_grant_across_organisations_is_inert_for_every_subject_kind(db):
    org_a = await make_org(db, "org-a")
    org_b = await make_org(db, "org-b")
    alice = await make_user(db, org_a, "alice@a.example")
    mallory = await make_user(db, org_b, "mallory@b.example")

    project = await make_project(db, org_a, alice)
    folder = await make_folder(db, project)
    document = await make_document(db, alice, project, folder)

    for subject_type, subject_id in (
        ("project", project.id),
        ("folder", folder.id),
        ("document", document.id),
    ):
        await make_grant(
            db,
            org_id=org_a.id,
            subject_type=subject_type,
            subject_id=subject_id,
            user=mallory,
            role="owner",
        )
        assert await effective_access(db, mallory.id, subject_type, subject_id) is None, (
            f"a cross-organisation grant on a {subject_type} must not resolve"
        )


# ───────────────────────────────── cascade ────────────────────────────────


async def test_folder_grant_cascades_to_documents_and_subfolders(db):
    """Editor on a folder is editor on everything beneath it."""
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, owner)
    top = await make_folder(db, project, "top")
    middle = await make_folder(db, project, "middle", parent=top)
    deep = await make_folder(db, project, "deep", parent=middle)

    in_top = await make_document(db, owner, project, top, "in top")
    in_deep = await make_document(db, owner, project, deep, "in deep")
    at_root = await make_document(db, owner, project, None, "at root")

    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=top.id,
        user_id=bob.id,
        role="editor",
    )

    assert await effective_access(db, bob.id, "folder", top.id) == "editor"
    assert await effective_access(db, bob.id, "folder", middle.id) == "editor"
    assert await effective_access(db, bob.id, "folder", deep.id) == "editor"
    assert await effective_access(db, bob.id, "document", in_top.id) == "editor"
    assert await effective_access(db, bob.id, "document", in_deep.id) == "editor"

    # Access flows down, never up or sideways.
    assert await effective_access(db, bob.id, "document", at_root.id) is None
    assert await effective_access(db, bob.id, "project", project.id) is None


async def test_project_grant_reaches_a_document_at_the_project_root(db):
    """folder_id NULL still inherits from the project."""
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, owner)
    rootless = await make_document(db, owner, project, None, "no folder")
    assert rootless.folder_id is None

    await grant_access(
        db,
        org_id=org.id,
        subject_type="project",
        subject_id=project.id,
        user_id=bob.id,
        role="editor",
    )

    assert await effective_access(db, bob.id, "document", rootless.id) == "editor"


# ──────────────────────────────── precedence ──────────────────────────────


async def test_the_most_generous_grant_wins(db):
    """Viewer on the project plus owner on one document: owner there, viewer elsewhere."""
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, owner)
    promoted = await make_document(db, owner, project, None, "promoted")
    ordinary = await make_document(db, owner, project, None, "ordinary")

    await grant_access(
        db,
        org_id=org.id,
        subject_type="project",
        subject_id=project.id,
        user_id=bob.id,
        role="viewer",
    )
    await grant_access(
        db,
        org_id=org.id,
        subject_type="document",
        subject_id=promoted.id,
        user_id=bob.id,
        role="owner",
    )

    assert await effective_access(db, bob.id, "document", promoted.id) == "owner"
    assert await effective_access(db, bob.id, "document", ordinary.id) == "viewer"
    assert await effective_access(db, bob.id, "project", project.id) == "viewer"


async def test_a_narrower_grant_does_not_reduce_a_wider_one(db):
    """Grants only ever add: viewer on a document under an editor folder stays editor."""
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, owner)
    folder = await make_folder(db, project)
    document = await make_document(db, owner, project, folder)

    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=folder.id,
        user_id=bob.id,
        role="editor",
    )
    await grant_access(
        db,
        org_id=org.id,
        subject_type="document",
        subject_id=document.id,
        user_id=bob.id,
        role="viewer",
    )

    assert await effective_access(db, bob.id, "document", document.id) == "editor"


# ──────────────────────────── absence and removal ──────────────────────────


async def test_no_grant_resolves_to_nothing(db):
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    stranger = await make_user(db, org, "stranger@org.example")

    project = await make_project(db, org, owner)
    folder = await make_folder(db, project)
    document = await make_document(db, owner, project, folder)

    assert await effective_access(db, stranger.id, "project", project.id) is None
    assert await effective_access(db, stranger.id, "folder", folder.id) is None
    assert await effective_access(db, stranger.id, "document", document.id) is None


async def test_revoking_a_grant_takes_effect_immediately(db):
    """Nothing is cached: the next call is the next query."""
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, owner)
    document = await make_document(db, owner, project)

    await grant_access(
        db,
        org_id=org.id,
        subject_type="document",
        subject_id=document.id,
        user_id=bob.id,
        role="editor",
    )
    assert await effective_access(db, bob.id, "document", document.id) == "editor"

    await revoke_access(
        db, subject_type="document", subject_id=document.id, user_id=bob.id
    )
    assert await effective_access(db, bob.id, "document", document.id) is None


async def test_regranting_replaces_the_role(db):
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, owner)

    for role in ("viewer", "owner", "editor"):
        await grant_access(
            db,
            org_id=org.id,
            subject_type="project",
            subject_id=project.id,
            user_id=bob.id,
            role=role,
        )
        assert await effective_access(db, bob.id, "project", project.id) == role

    count = (
        await db.execute(
            text("SELECT count(*) FROM access_grants WHERE user_id = :u"),
            {"u": str(bob.id)},
        )
    ).scalar_one()
    assert count == 1, "re-granting must move the existing row, not stack a second"


# ──────────────────────────── pathological input ───────────────────────────


async def test_missing_subjects_and_users_resolve_to_nothing(db):
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    nowhere = uuid.uuid4()

    assert await effective_access(db, alice.id, "document", nowhere) is None
    assert await effective_access(db, alice.id, "folder", nowhere) is None
    assert await effective_access(db, alice.id, "project", nowhere) is None
    assert await effective_access(db, nowhere, "project", nowhere) is None


async def test_a_parent_cycle_terminates(db):
    """A cycle in parent_folder_id must not hang the resolver."""
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")

    project = await make_project(db, org, owner)
    top = await make_folder(db, project, "top")
    middle = await make_folder(db, project, "middle", parent=top)
    document = await make_document(db, owner, project, middle)

    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=top.id,
        user_id=bob.id,
        role="viewer",
    )
    # Close the loop: top -> middle -> top.
    top.parent_folder_id = middle.id
    await db.flush()

    assert await effective_access(db, bob.id, "document", document.id) == "viewer"


async def test_the_ancestor_walk_is_depth_capped(db):
    """Beyond the cap a grant stops cascading, rather than costing unbounded work."""
    org = await make_org(db, "org")
    owner = await make_user(db, org, "owner@org.example")
    bob = await make_user(db, org, "bob@org.example")
    project = await make_project(db, org, owner)

    chain = [await make_folder(db, project, "f0")]
    for depth in range(1, 60):
        chain.append(await make_folder(db, project, f"f{depth}", parent=chain[-1]))
    document = await make_document(db, owner, project, chain[-1])

    # The root of a 60-deep chain is outside the 50-ancestor cap.
    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=chain[0].id,
        user_id=bob.id,
        role="editor",
    )
    assert await effective_access(db, bob.id, "document", document.id) is None

    # A grant inside the cap still reaches the document.
    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=chain[-3].id,
        user_id=bob.id,
        role="editor",
    )
    assert await effective_access(db, bob.id, "document", document.id) == "editor"


async def test_a_parent_in_another_project_carries_nothing(db):
    """The walk refuses to leave the project, whatever parent_folder_id says."""
    org = await make_org(db, "org")
    alice = await make_user(db, org, "alice@org.example")
    bob = await make_user(db, org, "bob@org.example")

    hers = await make_project(db, org, alice, "hers")
    his = await make_project(db, org, bob, "his")

    her_folder = await make_folder(db, hers, "her folder")
    document = await make_document(db, alice, hers, her_folder)
    his_folder = await make_folder(db, his, "his folder")

    await grant_access(
        db,
        org_id=org.id,
        subject_type="folder",
        subject_id=his_folder.id,
        user_id=bob.id,
        role="owner",
    )
    # Violate the service-layer invariant directly.
    her_folder.parent_folder_id = his_folder.id
    await db.flush()

    assert await effective_access(db, bob.id, "document", document.id) is None
