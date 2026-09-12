"""Block-level diff.

A block is one non-empty line of the flattened document, which maps 1:1 to a
top-level TipTap node. `SequenceMatcher` gives the opcodes; the post-processing
step is what makes the result readable — a `replace` of paired similar lines is
reported as one `modify` rather than an unrelated remove/add pair.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

# Below this ratio two lines are unrelated edits, not one modification.
_MODIFY_SIMILARITY = 0.45
# Longer unchanged runs collapse, keeping this much context on each side.
_CONTEXT_BLOCKS = 2


def blocks_of(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def diff_text(old: str, new: str) -> dict[str, Any]:
    old_blocks, new_blocks = blocks_of(old), blocks_of(new)
    raw: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old_blocks, new_blocks).get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                raw.append(
                    {
                        "kind": "equal",
                        "old_text": old_blocks[i1 + offset],
                        "new_text": new_blocks[j1 + offset],
                        "old_line": i1 + offset + 1,
                        "new_line": j1 + offset + 1,
                    }
                )
        elif tag == "delete":
            raw += [
                {"kind": "remove", "old_text": old_blocks[i], "old_line": i + 1}
                for i in range(i1, i2)
            ]
        elif tag == "insert":
            raw += [
                {"kind": "add", "new_text": new_blocks[j], "new_line": j + 1}
                for j in range(j1, j2)
            ]
        else:  # replace — pair up what is recognisably the same line reworded
            raw += _replace_blocks(old_blocks, new_blocks, i1, i2, j1, j2)

    additions = sum(1 for b in raw if b["kind"] == "add")
    deletions = sum(1 for b in raw if b["kind"] == "remove")
    modifications = sum(1 for b in raw if b["kind"] == "modify")

    return {
        "blocks": _collapse(raw) if (additions or deletions or modifications) else [],
        "additions": additions,
        "deletions": deletions,
        "modifications": modifications,
    }


def _replace_blocks(
    old_blocks: list[str], new_blocks: list[str], i1: int, i2: int, j1: int, j2: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pairs = min(i2 - i1, j2 - j1)

    for offset in range(pairs):
        old_text, new_text = old_blocks[i1 + offset], new_blocks[j1 + offset]
        if _similar(old_text, new_text) >= _MODIFY_SIMILARITY:
            out.append(
                {
                    "kind": "modify",
                    "old_text": old_text,
                    "new_text": new_text,
                    "old_line": i1 + offset + 1,
                    "new_line": j1 + offset + 1,
                }
            )
        else:
            out.append({"kind": "remove", "old_text": old_text, "old_line": i1 + offset + 1})
            out.append({"kind": "add", "new_text": new_text, "new_line": j1 + offset + 1})

    for i in range(i1 + pairs, i2):
        out.append({"kind": "remove", "old_text": old_blocks[i], "old_line": i + 1})
    for j in range(j1 + pairs, j2):
        out.append({"kind": "add", "new_text": new_blocks[j], "new_line": j + 1})
    return out


def _collapse(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace long unchanged runs with a single marker — read only what moved."""
    keep = [i for i, b in enumerate(blocks) if b["kind"] != "equal"]
    if not keep:
        return blocks

    visible: set[int] = set()
    for index in keep:
        for offset in range(-_CONTEXT_BLOCKS, _CONTEXT_BLOCKS + 1):
            if 0 <= index + offset < len(blocks):
                visible.add(index + offset)

    out: list[dict[str, Any]] = []
    hidden = 0
    for index, block in enumerate(blocks):
        if index in visible:
            if hidden:
                out.append({"kind": "collapsed", "count": hidden})
                hidden = 0
            out.append(block)
        else:
            hidden += 1
    if hidden:
        out.append({"kind": "collapsed", "count": hidden})
    return out
