"""What the platform itself produces, turned into retrieval chunks.

`app.services.documents.tiptap_to_text` already flattens a ProseMirror/TipTap
`content_json` tree to one diffable string — but a diff wants a single line
per top-level node, while a citation wants readable, properly spaced prose
with its heading intact. This module is a second, independent walker over
the same tree, tuned for that: `chunk_document` returns one chunk per heading
section (further windowed if a section runs long), each shaped exactly like
`app.rag.loaders.Page` so the store, and anything that reads a `Hit`, cannot
tell a document chunk from a file page.

`reindex_document` is the commit-time hook: chunk the current content, hand
it to the store's delete-then-upsert. It never raises — a commit must not
fail because Qdrant or the embedding call hiccuped, exactly as a file upload
records a failed index rather than blocking the request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.rag import store
from app.rag.loaders import Page

if TYPE_CHECKING:
    from app.db.models import Document

logger = logging.getLogger(__name__)

# A section longer than this is windowed rather than indexed as one chunk.
# Chosen to match app.rag.loaders' own text budget, for embeddings of a
# similar, retrieval-friendly size regardless of source.
_CHUNK_CHARS = 1500


@dataclass(slots=True)
class _Section:
    """Everything from one heading up to (not including) the next."""

    heading: str = ""
    blocks: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.heading.strip() and not self.blocks


# ─────────────────────────────── the walker ────────────────────────────────


def _inline_text(node: dict[str, Any]) -> str:
    """Concatenated text of a node's inline descendants.

    TipTap already carries the right spacing inside each text node's own
    `text`, so plain concatenation is correct for siblings; the one node that
    needs translating rather than recursing through is a hard line break.
    """
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    return "".join(_inline_text(child) for child in node.get("content") or [])


def _walk_block(node: dict[str, Any]) -> list[str]:
    """Plain-text line(s) for one block node.

    Never called on a heading: the caller decides section boundaries one
    level up, before a heading's content would reach here.
    """
    kind = node.get("type")

    if kind in ("paragraph", "codeBlock"):
        text = _inline_text(node).strip()
        return [text] if text else []

    if kind == "blockquote":
        lines: list[str] = []
        for child in node.get("content") or []:
            lines.extend(f"> {line}" for line in _walk_block(child))
        return lines

    if kind in ("bulletList", "orderedList"):
        lines = []
        for position, item in enumerate(node.get("content") or [], start=1):
            marker = f"{position}. " if kind == "orderedList" else "- "
            item_lines: list[str] = []
            for child in item.get("content") or []:
                item_lines.extend(_walk_block(child))
            if item_lines:
                lines.append(marker + item_lines[0])
                # A list item's own second-and-later block (a nested list, a
                # second paragraph) is indented under the marker rather than
                # started as a new top-level line.
                lines.extend(f"  {line}" for line in item_lines[1:])
        return lines

    if kind == "table":
        lines = []
        for row in node.get("content") or []:
            cells = [_inline_text(cell).strip() for cell in row.get("content") or []]
            if any(cells):
                lines.append(" | ".join(cells))
        return lines

    if kind in ("horizontalRule", "image"):
        return []

    # An editor extension this walker has not seen: best-effort plain text
    # rather than silently dropping the node.
    text = _inline_text(node).strip()
    return [text] if text else []


def _sections(doc: dict[str, Any]) -> list[_Section]:
    """Split the document at every heading, whatever its level.

    Flat, not hierarchical: an H2 does not nest under its H1. A document with
    only top-level headings would otherwise chunk as a handful of oversized
    sections, and citation precision wants the narrower unit — the window
    split below still catches a section that runs long regardless.
    """
    sections = [_Section()]
    for node in doc.get("content") or []:
        if node.get("type") == "heading":
            level = int((node.get("attrs") or {}).get("level", 1))
            marker = "#" * max(1, min(level, 6))
            heading_text = _inline_text(node).strip()
            sections.append(_Section(heading=f"{marker} {heading_text}".strip()))
            continue
        # One top-level node becomes one block, its own lines joined tightly
        # (a list's items, a table's rows) — the wider paragraph-level gap
        # `_windows` adds is only ever *between* separate top-level nodes.
        block_text = "\n".join(_walk_block(node))
        if block_text.strip():
            sections[-1].blocks.append(block_text)
    return [s for s in sections if not s.is_empty()]


def _windows(section: _Section) -> list[str]:
    """One chunk for a normal section; several for one over `_CHUNK_CHARS`.

    The heading is repeated at the top of every window so a chunk reads on
    its own — a citation should never be a wall of text with no idea what
    section it came from.
    """
    heading = section.heading
    if not section.blocks:
        return [heading] if heading.strip() else []

    windows: list[str] = []
    buffer: list[str] = []
    buffer_len = len(heading)
    for block in section.blocks:
        added = len(block) + 2  # the "\n\n" join
        if buffer and buffer_len + added > _CHUNK_CHARS:
            windows.append("\n\n".join([heading, *buffer]) if heading else "\n\n".join(buffer))
            buffer = []
            buffer_len = len(heading)
        buffer.append(block)
        buffer_len += added
    if buffer:
        windows.append("\n\n".join([heading, *buffer]) if heading else "\n\n".join(buffer))
    return windows


def chunk_document(content_json: dict[str, Any] | None) -> list[Page]:
    """ProseMirror `content_json` -> retrieval chunks, numbered from 1.

    Empty input, or content with no extractable text, returns `[]` — exactly
    how an empty upload yields no pages, so a blank document indexes nothing
    rather than one empty chunk.
    """
    if not content_json:
        return []
    texts = [w for section in _sections(content_json) for w in _windows(section) if w.strip()]
    return [Page(page=i, text=t) for i, t in enumerate(texts, start=1)]


# ────────────────────────── the commit-time hook ───────────────────────────


async def reindex_document(document: Document) -> None:
    """Chunk `document`'s current content and replace its Qdrant points.

    Called from inside `_commit()` after the new content is assigned onto
    `document`, before the transaction commits. Never raises: a parse or
    embedding failure is logged and swallowed, the same posture as a file
    upload's index step, so the commit that triggered this can never fail
    because indexing did.
    """
    try:
        pages = chunk_document(document.content_json)
        await store.index_document(
            document_id=document.id,
            org_id=document.org_id,
            project_id=document.project_id,
            owner_id=document.owner_id,
            title=document.title,
            pages=pages,
        )
    except Exception:  # noqa: BLE001 - a commit must never fail because indexing did
        logger.error("Reindexing failed for document %s", document.id, exc_info=True)
