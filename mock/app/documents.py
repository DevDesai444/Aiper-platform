"""TipTap document plumbing: Markdown in, flattened text out.

Agent output enters version control through `markdown_to_tiptap`, so the history
shows exactly where the machine stopped and a person took over.
"""

from __future__ import annotations

import re
from typing import Any

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")

_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), "bold"),
    (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"), "italic"),
    (re.compile(r"`(.+?)`"), "code"),
]


def empty_document() -> dict[str, Any]:
    return {"type": "doc", "content": [_paragraph("")]}


def _text_nodes(raw: str) -> list[dict[str, Any]]:
    """Split a line into text nodes, carrying bold/italic/code marks."""
    segments: list[dict[str, Any]] = [{"text": raw, "marks": []}]
    for pattern, mark in _INLINE:
        next_segments: list[dict[str, Any]] = []
        for segment in segments:
            if segment["marks"]:
                next_segments.append(segment)
                continue
            cursor = 0
            for match in pattern.finditer(segment["text"]):
                if match.start() > cursor:
                    next_segments.append(
                        {"text": segment["text"][cursor : match.start()], "marks": []}
                    )
                next_segments.append({"text": match.group(1), "marks": [mark]})
                cursor = match.end()
            next_segments.append({"text": segment["text"][cursor:], "marks": []})
        segments = next_segments

    nodes = []
    for segment in segments:
        if not segment["text"]:
            continue
        node: dict[str, Any] = {"type": "text", "text": segment["text"]}
        if segment["marks"]:
            node["marks"] = [{"type": m} for m in segment["marks"]]
        nodes.append(node)
    return nodes


def _paragraph(text: str) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "paragraph"}
    content = _text_nodes(text)
    if content:
        node["content"] = content
    return node


def _cell(text: str, header: bool) -> dict[str, Any]:
    return {
        "type": "tableHeader" if header else "tableCell",
        "content": [_paragraph(text.strip())],
    }


def markdown_to_tiptap(markdown: str) -> dict[str, Any]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    content: list[dict[str, Any]] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if not line.strip():
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": len(heading.group(1))},
                    "content": _text_nodes(heading.group(2).strip()),
                }
            )
            index += 1
            continue

        if _TABLE_ROW.match(line):
            rows: list[list[str]] = []
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                if not _TABLE_SEP.match(lines[index]):
                    cells = lines[index].strip().strip("|").split("|")
                    rows.append([c.strip() for c in cells])
                index += 1
            if rows:
                content.append(
                    {
                        "type": "table",
                        "content": [
                            {
                                "type": "tableRow",
                                "content": [_cell(c, header=(r == 0)) for c in row],
                            }
                            for r, row in enumerate(rows)
                        ],
                    }
                )
            continue

        if _BULLET.match(line) or _ORDERED.match(line):
            ordered = bool(_ORDERED.match(line))
            items: list[dict[str, Any]] = []
            while index < len(lines):
                match = _ORDERED.match(lines[index]) if ordered else _BULLET.match(lines[index])
                if not match:
                    break
                items.append({"type": "listItem", "content": [_paragraph(match.group(1).strip())]})
                index += 1
            content.append(
                {"type": "orderedList" if ordered else "bulletList", "content": items}
            )
            continue

        buffer = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not _is_block_start(lines[index]):
            buffer.append(lines[index].strip())
            index += 1
        content.append(_paragraph(" ".join(buffer)))

    return {"type": "doc", "content": content or [_paragraph("")]}


def _is_block_start(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _BULLET.match(line)
        or _ORDERED.match(line)
        or _TABLE_ROW.match(line)
    )


def tiptap_to_text(doc: dict[str, Any] | None) -> str:
    """Flatten to one line per top-level node — the unit the diff operates on."""
    if not doc:
        return ""
    lines: list[str] = []
    for node in doc.get("content", []) or []:
        lines.extend(_flatten(node))
    return "\n".join(line for line in lines if line.strip())


def _inline_text(node: dict[str, Any]) -> str:
    if node.get("type") == "text":
        return str(node.get("text", ""))
    return "".join(_inline_text(child) for child in node.get("content", []) or [])


def _flatten(node: dict[str, Any], prefix: str = "") -> list[str]:
    kind = node.get("type")

    if kind == "heading":
        level = int((node.get("attrs") or {}).get("level", 1))
        return [f"{'#' * level} {_inline_text(node)}".strip()]

    if kind in {"paragraph", "codeBlock", "blockquote"}:
        text = _inline_text(node)
        return [f"{prefix}{text}"] if text.strip() else []

    if kind in {"bulletList", "orderedList"}:
        lines: list[str] = []
        for position, item in enumerate(node.get("content", []) or [], start=1):
            marker = f"{position}. " if kind == "orderedList" else "- "
            for child in item.get("content", []) or []:
                lines.extend(_flatten(child, prefix=marker))
        return lines

    if kind == "table":
        lines = []
        for row in node.get("content", []) or []:
            cells = [_inline_text(cell) for cell in row.get("content", []) or []]
            lines.append("| " + " | ".join(cells) + " |")
        return lines

    text = _inline_text(node)
    return [text] if text.strip() else []
