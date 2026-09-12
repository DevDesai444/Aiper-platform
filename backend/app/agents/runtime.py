"""Translate the LangGraph event stream into the compact feed the UI renders."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents.orchestrator import build_agent
from app.agents.skills import SkillContext
from app.config import settings

TOOL_LABELS = {
    "search_pages": "Searching indexed pages",
    "find_evidence_in_sources": "Looking for evidence in the sources",
    "load_target_document": "Reading the target document",
    "list_attachments": "Reviewing attachments",
    "comparison_scope": "Confirming comparison scope",
    "list_templates": "Listing templates",
    "get_template": "Resolving the template",
    "build_compliance_matrix": "Building compliance matrix",
    "ls": "Listing workspace files",
    "read_file": "Reading workspace file",
    "write_file": "Writing workspace file",
    "edit_file": "Editing workspace file",
    "glob": "Finding workspace files",
    "grep": "Searching workspace files",
}

_MAX_DETAIL_CHARS = 1200
_SKILL_PREFIX = "/skills/"


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_DETAIL_CHARS else f"{text[:_MAX_DETAIL_CHARS]}…"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_stringify(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(_stringify(v) for v in value)
    return str(value)


def _todos(payload: Any) -> list[dict[str, str]]:
    items = payload.get("todos") if isinstance(payload, dict) else None
    return [
        {"content": str(i.get("content", "")), "status": str(i.get("status", "pending"))}
        for i in (items if isinstance(items, list) else [])
        if isinstance(i, dict)
    ]


def _skill_name(payload: Any) -> str | None:
    path = str(payload.get("file_path", "")) if isinstance(payload, dict) else ""
    if path.startswith(_SKILL_PREFIX) and path.endswith("SKILL.md"):
        return path[len(_SKILL_PREFIX) :].split("/", 1)[0]
    return None


def _chunk_text(chunk: Any) -> str:
    text = getattr(chunk, "text", None)
    if callable(text):
        text = text()
    if not text and chunk is not None:
        content = getattr(chunk, "content", "")
        text = content if isinstance(content, str) else ""
    return text or ""


async def run_turn(
    *, ctx: SkillContext, history: list, question: str
) -> AsyncIterator[dict[str, Any]]:
    """Yield UI events for one agent turn.

    Nesting comes from `parent_ids`: a tool call whose ancestry includes an open
    `task` run belongs to that sub-agent and is emitted with `parent`. Tokens
    are streamed only from the supervisor — sub-agent and summarizer output
    would interleave with the answer.
    """
    agent = build_agent(ctx)
    open_tasks: set[str] = set()
    skill_reads: set[str] = set()

    yield {"type": "status", "value": "running"}

    async for event in agent.astream_events(
        {"messages": [*history, HumanMessage(content=question)]},
        version="v2",
        config={"recursion_limit": settings.agent_recursion_limit},
    ):
        kind = event.get("event")
        name = event.get("name", "")
        run_id = str(event.get("run_id", ""))
        parents = [str(p) for p in event.get("parent_ids") or []]
        parent = next((p for p in reversed(parents) if p in open_tasks), None)

        if kind == "on_tool_start":
            payload = (event.get("data") or {}).get("input") or {}
            if name == "write_todos":
                yield {"type": "plan", "id": run_id, "items": _todos(payload)}
            elif name == "task":
                open_tasks.add(run_id)
                yield {
                    "type": "delegation",
                    "id": run_id,
                    "agent": str(payload.get("subagent_type") or "agent"),
                    "task": _clip(_stringify(payload.get("description"))),
                    "status": "running",
                }
            elif name == "read_file" and (skill := _skill_name(payload)):
                skill_reads.add(run_id)
                yield {"type": "skill", "id": run_id, "skill": skill}
            else:
                yield {
                    "type": "tool_start",
                    "id": run_id,
                    "tool": name,
                    "label": TOOL_LABELS.get(name, name.replace("_", " ").capitalize()),
                    "detail": _clip(_stringify(payload)),
                    **({"parent": parent} if parent else {}),
                }

        elif kind == "on_tool_end":
            if name == "write_todos" or run_id in skill_reads:
                continue
            output = _clip(_stringify((event.get("data") or {}).get("output")))
            if name == "task":
                open_tasks.discard(run_id)
                yield {"type": "delegation_end", "id": run_id, "output": output}
            else:
                yield {"type": "tool_end", "id": run_id, "tool": name, "output": output}

        elif kind == "on_chat_model_stream" and parent is None and "summary" not in (event.get("tags") or []):
            text = _chunk_text((event.get("data") or {}).get("chunk"))
            if text:
                yield {"type": "token", "value": text}

    yield {"type": "status", "value": "done"}
