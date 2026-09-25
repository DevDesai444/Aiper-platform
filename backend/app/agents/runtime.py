"""Translate the LangGraph event stream into the compact feed the UI renders.

Two layers here:

* :func:`run_turn` — the raw event → UI-event translator. Given a built agent
  (or a factory), streams the graph and yields the same activity dicts the
  chat route persists into ``chat_messages.activity``.
* :func:`run_agent_turn` — the guardrail wrapper. Adds a wall-clock timeout,
  the per-user daily token budget, per-role token accounting, a final
  ``{"type": "usage", ...}`` activity event, one structured JSON log line per
  turn, and safe client-facing failure surfaces on any exception (including
  ``CancelledError`` on client disconnect).

The wrapper is the seam production calls: :mod:`app.api.chat` invokes
``run_agent_turn`` and the tests exercise it with a stub agent factory so no
Azure or deepagents graph runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents.budget import DailyTokenBudget, default_budget
from app.agents.skills import SkillContext
from app.config import settings

logger = logging.getLogger(__name__)

# Public role labels for the usage event and structured log. Anything the
# accumulator cannot attribute (unknown tags/task type) is grouped as "other".
ROLE_SUPERVISOR = "supervisor"
ROLE_SUMMARY = "summary"
ROLE_OTHER = "other"
_KNOWN_SUBAGENT_ROLES = {"research", "document_writer", "comparison_analyst"}

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

# Client-facing copy for the two failure surfaces. Intentionally short and
# non-technical; the server-side logger still gets the full traceback.
_TIMEOUT_MESSAGE = "The agent ran out of time for this turn."
_ERROR_MESSAGE = "The agent hit an unexpected error and could not finish this turn."
_BUDGET_MESSAGE = (
    "Your daily model-usage budget is exhausted. The limit resets at 00:00 UTC. "
    "Try again then, or ask an admin to raise your cap."
)


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


# ─────────────────────────────── usage tracking ───────────────────────────────


@dataclass
class _RoleTotals:
    prompt: int = 0
    completion: int = 0
    total: int = 0


@dataclass
class UsageAccumulator:
    """Per-role prompt/completion/total token tallies for one turn.

    Roles: ``supervisor`` (top-level graph model), ``research`` /
    ``document_writer`` / ``comparison_analyst`` (deepagents sub-agents), and
    ``summary`` (the summarization middleware's model). Anything else lands in
    ``other`` — that group being non-empty is a sign a new sub-agent shipped
    without a matching label here.
    """

    by_role: dict[str, _RoleTotals] = field(default_factory=dict)

    def record(self, role: str, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        totals = self.by_role.setdefault(role, _RoleTotals())
        totals.prompt += int(usage.get("input_tokens", 0) or 0)
        totals.completion += int(usage.get("output_tokens", 0) or 0)
        # Some providers omit total_tokens; fall back to prompt + completion so
        # the caller can trust the invariant total == prompt + completion when
        # they need it.
        total = usage.get("total_tokens")
        totals.total += int(total) if total is not None else (
            int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
        )

    @property
    def total_tokens(self) -> int:
        return sum(t.total for t in self.by_role.values())

    def as_event(self, *, elapsed_seconds: float | None = None) -> dict[str, Any]:
        return {
            "type": "usage",
            "by_role": {
                role: {"prompt": t.prompt, "completion": t.completion, "total": t.total}
                for role, t in self.by_role.items()
            },
            "total": self.total_tokens,
            **({"elapsed_s": round(elapsed_seconds, 3)} if elapsed_seconds is not None else {}),
        }


def _extract_usage(output: Any) -> dict[str, Any] | None:
    """Pull ``usage_metadata`` from a chat-model event's ``data.output``.

    LangChain 1.x surfaces usage on ``AIMessage.usage_metadata`` and (for
    streaming) on the aggregated ``AIMessageChunk`` at ``on_chat_model_end``.
    We also handle a ``ChatResult`` shape for older adapters.
    """
    if output is None:
        return None
    usage = getattr(output, "usage_metadata", None)
    if usage:
        return dict(usage)
    generations = getattr(output, "generations", None)
    if generations:
        try:
            msg = generations[0][0].message  # type: ignore[index]
        except (IndexError, AttributeError, TypeError):
            return None
        inner = getattr(msg, "usage_metadata", None)
        return dict(inner) if inner else None
    return None


def _classify_role(
    event_tags: list[str], parent_ids: list[str], open_tasks: dict[str, str]
) -> str:
    """Assign one of the known roles to a chat-model event.

    ``summary`` tag wins because the summarization middleware carries it on
    every one of its calls; otherwise we walk the parent chain and use the
    innermost open sub-agent task's name.
    """
    if event_tags and "summary" in event_tags:
        return ROLE_SUMMARY
    for pid in reversed(parent_ids):
        role = open_tasks.get(pid)
        if role:
            return role if role in _KNOWN_SUBAGENT_ROLES else ROLE_OTHER
    return ROLE_SUPERVISOR


# ───────────────────────────────── streaming ──────────────────────────────────


async def run_turn(
    *,
    ctx: SkillContext,
    history: list,
    question: str,
    agent_factory: Callable[[SkillContext], Any] | None = None,
    recursion_limit: int | None = None,
    usage: UsageAccumulator | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield UI events for one agent turn.

    Nesting comes from ``parent_ids``: a tool call whose ancestry includes an
    open ``task`` run belongs to that sub-agent and is emitted with ``parent``.
    Tokens are streamed only from the supervisor — sub-agent and summarizer
    output would interleave with the answer.

    Parameters
    ----------
    agent_factory:
        Callable that returns a compiled graph exposing
        ``astream_events(input, version, config) -> AsyncIterator[dict]``.
        Defaults to :func:`app.agents.orchestrator.build_agent`; tests inject a
        stub so no Azure / deepagents graph runs.
    recursion_limit:
        Value passed as ``config["recursion_limit"]`` to the graph invocation.
        Defaults to :attr:`app.config.Settings.agent_recursion_limit`.
    usage:
        Optional accumulator; if provided, per-role token totals are recorded
        into it as ``on_chat_model_end`` events pass by. Does not yield a
        ``usage`` event on its own — that is :func:`run_agent_turn`'s job.
    """
    if agent_factory is None:
        # Import here to keep the deepagents module optional for unit tests
        # that inject a stub factory.
        from app.agents.orchestrator import build_agent as agent_factory  # noqa: N806

    agent = agent_factory(ctx)
    open_tasks: dict[str, str] = {}
    skill_reads: set[str] = set()
    limit = recursion_limit if recursion_limit is not None else settings.agent_recursion_limit

    yield {"type": "status", "value": "running"}

    async for event in agent.astream_events(
        {"messages": [*history, HumanMessage(content=question)]},
        version="v2",
        config={"recursion_limit": limit},
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
                sub = str(payload.get("subagent_type") or "agent")
                open_tasks[run_id] = sub
                yield {
                    "type": "delegation",
                    "id": run_id,
                    "agent": sub,
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
                open_tasks.pop(run_id, None)
                yield {"type": "delegation_end", "id": run_id, "output": output}
            else:
                yield {"type": "tool_end", "id": run_id, "tool": name, "output": output}

        elif kind == "on_chat_model_stream":
            tags = [str(t) for t in (event.get("tags") or [])]
            if parent is None and "summary" not in tags:
                text = _chunk_text((event.get("data") or {}).get("chunk"))
                if text:
                    yield {"type": "token", "value": text}

        elif kind == "on_chat_model_end" and usage is not None:
            tags = [str(t) for t in (event.get("tags") or [])]
            role = _classify_role(tags, parents, open_tasks)
            usage.record(role, _extract_usage((event.get("data") or {}).get("output")))

    yield {"type": "status", "value": "done"}


# ───────────────────────────────── guardrails ──────────────────────────────────


async def run_agent_turn(
    *,
    ctx: SkillContext,
    history: list,
    question: str,
    user_id: uuid.UUID | str | None = None,
    session_id: uuid.UUID | str | None = None,
    agent_factory: Callable[[SkillContext], Any] | None = None,
    budget: DailyTokenBudget | None = None,
    timeout_seconds: float | None = None,
    recursion_limit: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> AsyncIterator[dict[str, Any]]:
    """Run one agent turn with production guardrails.

    Yields the same UI activity events as :func:`run_turn`, followed by a
    terminal ``{"type": "usage", ...}`` event that carries per-role token
    totals and the wall-clock elapsed time. On failure the terminal event is
    still emitted and includes any usage recorded up to that point.

    Failure surfaces:

    * **Over daily budget** — a single ``{"type": "budget", ...}`` refusal event
      is emitted before the agent is built, and no model call runs.
    * **Wall-clock timeout** — the graph is cancelled and a single
      ``{"type": "timeout", ...}`` event is emitted; anything the graph had
      yielded up to that point is still surfaced.
    * **Any other exception** — a single ``{"type": "error", ...}`` event with
      generic client-facing copy; the full traceback goes to the server log.
    * **Client disconnect** (``CancelledError``) — propagated after the agent
      task is cancelled and partial usage is logged.
    """
    budget = budget if budget is not None else default_budget()
    timeout = timeout_seconds if timeout_seconds is not None else settings.agent_turn_timeout_seconds
    usage = UsageAccumulator()
    started = monotonic()

    # 0. Daily-cap gate — before any model spend.
    allowed, remaining = budget.check(user_id)
    if not allowed:
        event = {
            "type": "budget",
            "message": _BUDGET_MESSAGE,
            "cap": budget.cap,
            "remaining": remaining,
        }
        yield {"type": "status", "value": "running"}
        yield event
        yield {"type": "status", "value": "done"}
        yield usage.as_event(elapsed_seconds=monotonic() - started)
        _log_turn(
            session_id=session_id,
            user_id=user_id,
            usage=usage,
            elapsed=monotonic() - started,
            outcome="refused_over_budget",
        )
        return

    stream = run_turn(
        ctx=ctx,
        history=history,
        question=question,
        agent_factory=agent_factory,
        recursion_limit=recursion_limit,
        usage=usage,
    )

    outcome = "ok"
    try:
        async with asyncio.timeout(timeout):
            async for event in stream:
                yield event
    except TimeoutError:
        outcome = "timeout"
        logger.warning(
            "Agent turn timed out after %.1fs (session=%s user=%s)",
            timeout,
            session_id,
            user_id,
        )
        # Best-effort close of the generator so the underlying graph task is
        # cancelled promptly. aclose can raise if the generator is already
        # closed or is finishing an unrelated exception; suppress those so we
        # never mask the timeout with a shutdown error.
        with contextlib.suppress(Exception):
            await stream.aclose()
        yield {"type": "timeout", "message": _TIMEOUT_MESSAGE, "seconds": timeout}
    except asyncio.CancelledError:
        outcome = "cancelled"
        logger.info(
            "Agent turn cancelled (client disconnect) session=%s user=%s",
            session_id,
            user_id,
        )
        with contextlib.suppress(Exception):
            await stream.aclose()
        # Record whatever usage we accumulated before the disconnect, then let
        # the cancellation propagate so the ASGI layer can tear down the response.
        elapsed = monotonic() - started
        budget.record(user_id, usage.total_tokens)
        _log_turn(
            session_id=session_id,
            user_id=user_id,
            usage=usage,
            elapsed=elapsed,
            outcome=outcome,
        )
        raise
    except Exception:  # noqa: BLE001 - the whole point is to catch anything
        outcome = "error"
        logger.exception(
            "Agent turn failed (session=%s user=%s)", session_id, user_id
        )
        with contextlib.suppress(Exception):
            await stream.aclose()
        yield {"type": "error", "message": _ERROR_MESSAGE}

    elapsed = monotonic() - started
    budget.record(user_id, usage.total_tokens)
    yield usage.as_event(elapsed_seconds=elapsed)
    _log_turn(
        session_id=session_id,
        user_id=user_id,
        usage=usage,
        elapsed=elapsed,
        outcome=outcome,
    )


def _log_turn(
    *,
    session_id: uuid.UUID | str | None,
    user_id: uuid.UUID | str | None,
    usage: UsageAccumulator,
    elapsed: float,
    outcome: str,
) -> None:
    """One structured JSON log line per turn — cost accounting's durable record."""
    record = {
        "event": "agent_turn",
        "session_id": str(session_id) if session_id is not None else None,
        "user_id": str(user_id) if user_id is not None else None,
        "outcome": outcome,
        "elapsed_s": round(elapsed, 3),
        "total_tokens": usage.total_tokens,
        "by_role": {
            role: {"prompt": t.prompt, "completion": t.completion, "total": t.total}
            for role, t in usage.by_role.items()
        },
    }
    logger.info("agent_turn %s", json.dumps(record, default=str, sort_keys=True))


# Preserve the module-level export used by the chat router.
__all__ = [
    "TOOL_LABELS",
    "UsageAccumulator",
    "run_agent_turn",
    "run_turn",
]
