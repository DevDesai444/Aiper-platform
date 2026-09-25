"""Unit tests for the E6 agent-runtime guardrails.

No Postgres, no Azure, no deepagents graph: every test injects a fake agent
whose ``astream_events`` yields a scripted event sequence (mimicking what a
real graph would emit), and asserts on the wrapper's behaviour.

The wrapper under test lives in :mod:`app.agents.runtime`. The public surface
we exercise is :func:`run_agent_turn` (production entry point) and
:class:`UsageAccumulator` / :class:`DailyTokenBudget` (its collaborators).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import pytest

from app.agents.budget import DailyTokenBudget
from app.agents.runtime import (
    ROLE_SUMMARY,
    ROLE_SUPERVISOR,
    UsageAccumulator,
    _classify_role,
    _extract_usage,
    run_agent_turn,
)
from app.agents.skills import SkillContext
from app.config import settings


# ─────────────────────────────── shared fakes ─────────────────────────────────


class _FakeAIMessage:
    """Just enough of AIMessage to satisfy ``_extract_usage``."""

    def __init__(self, usage: dict[str, int] | None):
        self.usage_metadata = usage
        self.content = ""


def _model_end_event(
    *,
    role: str,
    tokens_in: int,
    tokens_out: int,
    parent_ids: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build an ``on_chat_model_end`` event that classifies to ``role``.

    Role is either ``"supervisor"`` (no parent, no summary tag), ``"summary"``
    (summary tag), or one of the sub-agent names — in which case
    ``parent_ids`` must reference an already-opened ``task`` run_id.
    """
    return {
        "event": "on_chat_model_end",
        "name": "AzureChatOpenAI",
        "run_id": f"model-{role}-{tokens_in}-{tokens_out}",
        "parent_ids": parent_ids or [],
        "tags": tags or [],
        "data": {
            "output": _FakeAIMessage(
                {
                    "input_tokens": tokens_in,
                    "output_tokens": tokens_out,
                    "total_tokens": tokens_in + tokens_out,
                }
            )
        },
    }


def _task_start_event(*, run_id: str, subagent_type: str) -> dict[str, Any]:
    return {
        "event": "on_tool_start",
        "name": "task",
        "run_id": run_id,
        "parent_ids": [],
        "data": {"input": {"subagent_type": subagent_type, "description": "sub"}},
    }


def _task_end_event(*, run_id: str) -> dict[str, Any]:
    return {
        "event": "on_tool_end",
        "name": "task",
        "run_id": run_id,
        "parent_ids": [],
        "data": {"output": "done"},
    }


class _StubAgent:
    """Fake compiled graph whose ``astream_events`` replays a scripted list.

    ``config`` from the invocation is captured on ``self.captured_config`` so
    tests can assert the recursion_limit was passed through.
    """

    def __init__(
        self,
        events: list[dict[str, Any]],
        *,
        hang_after: int | None = None,
        raise_after: int | None = None,
        exc: type[Exception] = RuntimeError,
    ):
        self._events = events
        self._hang_after = hang_after
        self._raise_after = raise_after
        self._exc = exc
        self.captured_config: dict[str, Any] | None = None
        self.captured_input: dict[str, Any] | None = None

    async def astream_events(
        self,
        input: dict[str, Any],
        *,
        version: str,
        config: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        self.captured_config = dict(config)
        self.captured_input = input
        # One extra step past the last event, so ``hang_after`` / ``raise_after``
        # can point at "after the last yield".
        for i in range(len(self._events) + 1):
            if self._raise_after is not None and i == self._raise_after:
                raise self._exc("scripted failure")
            if self._hang_after is not None and i == self._hang_after:
                # Never returns unless cancelled — asyncio.timeout will do so.
                await asyncio.Event().wait()
            if i < len(self._events):
                yield self._events[i]


def _ctx() -> SkillContext:
    return SkillContext(owner_id=uuid.uuid4())


def _collect(gen: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sync helper to drain an async generator into a list."""

    async def _drain() -> list[dict[str, Any]]:
        return [event async for event in gen]

    return asyncio.run(_drain())


# ────────────────────────────── UsageAccumulator ──────────────────────────────


class TestUsageAccumulator:
    def test_accumulates_per_role_independently(self) -> None:
        u = UsageAccumulator()
        u.record("supervisor", {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130})
        u.record("supervisor", {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50})
        u.record("research", {"input_tokens": 500, "output_tokens": 20, "total_tokens": 520})

        assert u.by_role["supervisor"].prompt == 140
        assert u.by_role["supervisor"].completion == 40
        assert u.by_role["supervisor"].total == 180
        assert u.by_role["research"].total == 520
        assert u.total_tokens == 700

    def test_falls_back_when_total_missing(self) -> None:
        u = UsageAccumulator()
        u.record("supervisor", {"input_tokens": 3, "output_tokens": 4})
        assert u.by_role["supervisor"].total == 7  # 3 + 4

    def test_ignores_none_and_empty(self) -> None:
        u = UsageAccumulator()
        u.record("supervisor", None)
        u.record("supervisor", {})
        assert u.total_tokens == 0
        assert "supervisor" not in u.by_role

    def test_event_shape(self) -> None:
        u = UsageAccumulator()
        u.record("supervisor", {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        event = u.as_event(elapsed_seconds=1.234)
        assert event == {
            "type": "usage",
            "by_role": {"supervisor": {"prompt": 10, "completion": 5, "total": 15}},
            "total": 15,
            "elapsed_s": 1.234,
        }


# ─────────────────────────────── role classifier ──────────────────────────────


class TestRoleClassifier:
    def test_summary_tag_wins(self) -> None:
        assert _classify_role(["summary"], ["someparent"], {"someparent": "research"}) == ROLE_SUMMARY

    def test_open_subagent_parent(self) -> None:
        assert _classify_role([], ["task-run-1"], {"task-run-1": "research"}) == "research"

    def test_supervisor_when_no_matching_parent(self) -> None:
        assert _classify_role([], ["some-unrelated"], {}) == ROLE_SUPERVISOR

    def test_unknown_subagent_grouped_as_other(self) -> None:
        assert _classify_role([], ["t1"], {"t1": "future_agent"}) == "other"


# ─────────────────────────────── usage extractor ──────────────────────────────


class TestExtractUsage:
    def test_from_ai_message(self) -> None:
        msg = _FakeAIMessage({"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
        assert _extract_usage(msg) == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}

    def test_none_when_missing(self) -> None:
        assert _extract_usage(_FakeAIMessage(None)) is None
        assert _extract_usage(None) is None


# ─────────────────────────────── daily budget ────────────────────────────────


class TestDailyTokenBudget:
    def test_cap_boundary_cap_minus_one_passes(self) -> None:
        budget = DailyTokenBudget(cap=100)
        uid = uuid.uuid4()
        # 99 tokens consumed — one short of the cap.
        budget.record(uid, 99)
        allowed, remaining = budget.check(uid)
        assert allowed is True
        assert remaining == 1

    def test_cap_boundary_at_and_over_cap_refuses(self) -> None:
        budget = DailyTokenBudget(cap=100)
        uid = uuid.uuid4()
        budget.record(uid, 100)  # exactly at cap → no room left
        allowed, remaining = budget.check(uid)
        assert allowed is False
        assert remaining == 0

        # Going over cap does not underflow the remaining view.
        budget.record(uid, 50)
        allowed, remaining = budget.check(uid)
        assert allowed is False
        assert remaining == 0

    def test_disabled_cap_always_allows(self) -> None:
        budget = DailyTokenBudget(cap=0)
        uid = uuid.uuid4()
        budget.record(uid, 10**9)
        allowed, _ = budget.check(uid)
        assert allowed is True

    def test_per_user_isolation(self) -> None:
        budget = DailyTokenBudget(cap=100)
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        budget.record(u1, 100)
        assert budget.check(u1)[0] is False
        assert budget.check(u2)[0] is True  # unrelated user unaffected

    def test_day_rollover_resets_used(self) -> None:
        current = {"today": date(2026, 9, 24)}
        budget = DailyTokenBudget(cap=100, now=lambda: current["today"])
        uid = uuid.uuid4()

        budget.record(uid, 100)
        assert budget.check(uid)[0] is False

        # Advance the clock past midnight UTC → the user's tally resets.
        current["today"] = date(2026, 9, 25)
        allowed, remaining = budget.check(uid)
        assert allowed is True
        assert remaining == 100

        # And recording again on the new day accumulates from zero.
        budget.record(uid, 40)
        _, remaining = budget.check(uid)
        assert remaining == 60

    def test_day_backdate_also_resets(self) -> None:
        # Realistic clocks never go backwards, but the rollover check compares
        # equality — an operator swapping time zones shouldn't corrupt state.
        current = {"today": date(2026, 9, 25)}
        budget = DailyTokenBudget(cap=100, now=lambda: current["today"])
        uid = uuid.uuid4()
        budget.record(uid, 100)
        current["today"] = date(2026, 9, 24)
        assert budget.check(uid)[0] is True

    def test_snapshot_matches_last_record(self) -> None:
        current = {"today": date(2026, 9, 24)}
        budget = DailyTokenBudget(cap=1000, now=lambda: current["today"])
        uid = uuid.uuid4()
        budget.record(uid, 700)
        day, used = budget.snapshot(uid)
        assert day == date(2026, 9, 24)
        assert used == 700


# ────────────────────────────── run_agent_turn ────────────────────────────────


class TestRunAgentTurnHappyPath:
    def test_recursion_limit_reaches_the_graph(self) -> None:
        """The recursion_limit setting must land on the invocation config."""
        agent = _StubAgent(events=[])
        events = _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uuid.uuid4(),
                agent_factory=lambda _ctx: agent,
                budget=DailyTokenBudget(cap=0),
            )
        )
        # Config was passed at least once and carries the configured limit.
        assert agent.captured_config == {"recursion_limit": settings.agent_recursion_limit}
        # And the wrapper wrapped the transcript with a status frame in each end.
        assert events[0] == {"type": "status", "value": "running"}
        assert events[-2] == {"type": "status", "value": "done"}
        # The last event is always the usage frame.
        assert events[-1]["type"] == "usage"

    def test_recursion_limit_override(self) -> None:
        """Callers can override the limit per-turn."""
        agent = _StubAgent(events=[])
        _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uuid.uuid4(),
                agent_factory=lambda _ctx: agent,
                budget=DailyTokenBudget(cap=0),
                recursion_limit=7,
            )
        )
        assert agent.captured_config == {"recursion_limit": 7}

    def test_usage_accumulates_across_roles(self) -> None:
        """A scripted mix of supervisor / subagent / summary events tallies correctly."""
        research_run = "task-research-1"
        script = [
            _model_end_event(role="supervisor", tokens_in=100, tokens_out=20),
            _task_start_event(run_id=research_run, subagent_type="research"),
            _model_end_event(
                role="research",
                tokens_in=800,
                tokens_out=60,
                parent_ids=[research_run],
            ),
            _model_end_event(
                role="research",
                tokens_in=100,
                tokens_out=10,
                parent_ids=[research_run],
            ),
            _task_end_event(run_id=research_run),
            _model_end_event(role="summary", tokens_in=50, tokens_out=5, tags=["summary"]),
            _model_end_event(role="supervisor", tokens_in=200, tokens_out=40),
        ]
        agent = _StubAgent(events=script)
        events = _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uuid.uuid4(),
                agent_factory=lambda _ctx: agent,
                budget=DailyTokenBudget(cap=0),
            )
        )
        usage = next(e for e in events if e["type"] == "usage")
        # 100+200 in / 20+40 out
        assert usage["by_role"]["supervisor"] == {"prompt": 300, "completion": 60, "total": 360}
        # 800+100 in / 60+10 out
        assert usage["by_role"]["research"] == {"prompt": 900, "completion": 70, "total": 970}
        assert usage["by_role"]["summary"] == {"prompt": 50, "completion": 5, "total": 55}
        assert usage["total"] == 360 + 970 + 55
        assert "elapsed_s" in usage

    def test_usage_added_to_budget(self) -> None:
        """The wrapper records the turn's tokens against the user's daily budget."""
        script = [_model_end_event(role="supervisor", tokens_in=100, tokens_out=25)]
        agent = _StubAgent(events=script)
        budget = DailyTokenBudget(cap=1000)
        uid = uuid.uuid4()

        _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uid,
                agent_factory=lambda _ctx: agent,
                budget=budget,
            )
        )
        _, used = budget.snapshot(uid)
        assert used == 125


class TestRunAgentTurnBudget:
    def test_over_cap_short_circuits(self) -> None:
        """When the caller is over budget, no model event ever runs."""
        budget = DailyTokenBudget(cap=100)
        uid = uuid.uuid4()
        budget.record(uid, 100)  # exhaust
        agent = _StubAgent(
            events=[_model_end_event(role="supervisor", tokens_in=999, tokens_out=999)]
        )

        events = _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uid,
                agent_factory=lambda _ctx: agent,
                budget=budget,
            )
        )
        # The graph was never invoked.
        assert agent.captured_config is None
        # A refusal event and a zeroed usage event were emitted.
        types = [e["type"] for e in events]
        assert "budget" in types
        assert types.count("usage") == 1
        refusal = next(e for e in events if e["type"] == "budget")
        assert refusal["cap"] == 100
        # The usage event is present but empty (no model calls happened).
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["total"] == 0

    def test_cap_minus_one_still_runs(self) -> None:
        budget = DailyTokenBudget(cap=100)
        uid = uuid.uuid4()
        budget.record(uid, 99)
        agent = _StubAgent(events=[_model_end_event(role="supervisor", tokens_in=5, tokens_out=1)])
        events = _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uid,
                agent_factory=lambda _ctx: agent,
                budget=budget,
            )
        )
        # Turn actually ran and no budget event appears.
        assert agent.captured_config is not None
        assert not any(e["type"] == "budget" for e in events)


class TestRunAgentTurnTimeout:
    def test_timeout_emits_clean_event(self) -> None:
        """A wall-clock timeout ends the turn with a single 'timeout' activity."""
        # First event goes through; then the agent hangs forever.
        script = [_model_end_event(role="supervisor", tokens_in=10, tokens_out=2)]
        agent = _StubAgent(events=script, hang_after=1)
        events = _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uuid.uuid4(),
                agent_factory=lambda _ctx: agent,
                budget=DailyTokenBudget(cap=0),
                timeout_seconds=0.05,
            )
        )
        types = [e["type"] for e in events]
        assert "timeout" in types, f"expected timeout event, got: {types}"
        # Timeout event carries the configured budget for the client to display.
        timeout = next(e for e in events if e["type"] == "timeout")
        assert timeout["seconds"] == 0.05
        # And the terminal usage event is still emitted; it holds partial usage.
        assert events[-1]["type"] == "usage"
        assert events[-1]["total"] == 12


class TestRunAgentTurnException:
    def test_error_emits_safe_event(self) -> None:
        """An exception inside the graph never propagates; the client gets a
        generic message and the turn still ends cleanly."""
        agent = _StubAgent(
            events=[_model_end_event(role="supervisor", tokens_in=3, tokens_out=1)],
            raise_after=1,
            exc=RuntimeError,
        )
        events = _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uuid.uuid4(),
                agent_factory=lambda _ctx: agent,
                budget=DailyTokenBudget(cap=0),
            )
        )
        types = [e["type"] for e in events]
        assert "error" in types
        err = next(e for e in events if e["type"] == "error")
        # Traceback details never leak to the client.
        assert "message" in err
        assert "RuntimeError" not in err["message"]
        assert "scripted" not in err["message"]
        # Usage still terminates the feed.
        assert events[-1]["type"] == "usage"

    def test_error_event_before_any_model_call(self) -> None:
        """Exception on the first event: usage is zero but the shape still holds."""
        # The stub raises just before yielding events[0], so no model-end event
        # ever reaches the accumulator.
        agent = _StubAgent(
            events=[_model_end_event(role="supervisor", tokens_in=1, tokens_out=1)],
            raise_after=0,
            exc=ValueError,
        )
        events = _collect(
            run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uuid.uuid4(),
                agent_factory=lambda _ctx: agent,
                budget=DailyTokenBudget(cap=0),
            )
        )
        assert any(e["type"] == "error" for e in events)
        assert events[-1] == {
            "type": "usage",
            "by_role": {},
            "total": 0,
            "elapsed_s": events[-1]["elapsed_s"],
        }


class TestRunAgentTurnCancellation:
    def test_client_disconnect_propagates(self) -> None:
        """A CancelledError from outside (client hang up) is re-raised after
        the wrapper cancels the graph and logs partial usage."""

        async def _run() -> None:
            agent = _StubAgent(
                events=[_model_end_event(role="supervisor", tokens_in=8, tokens_out=2)],
                hang_after=1,
            )
            budget = DailyTokenBudget(cap=1000)
            uid = uuid.uuid4()
            gen = run_agent_turn(
                ctx=_ctx(),
                history=[],
                question="hi",
                user_id=uid,
                agent_factory=lambda _ctx: agent,
                budget=budget,
            )

            async def _consume() -> None:
                async for _ in gen:
                    pass

            task = asyncio.create_task(_consume())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Partial usage was still counted against the caller's budget.
            _, used = budget.snapshot(uid)
            assert used == 10

        asyncio.run(_run())


class TestConfigDefaults:
    def test_defaults_present(self) -> None:
        """The settings the wrapper depends on are declared and typed."""
        assert isinstance(settings.agent_turn_timeout_seconds, int)
        assert settings.agent_turn_timeout_seconds > 0
        assert isinstance(settings.agent_daily_token_cap, int)
        assert settings.agent_daily_token_cap >= 0
        # And the recursion limit is still there (E6 verified it wires through).
        assert settings.agent_recursion_limit > 0

    def test_new_day_is_a_calendar_day_apart(self) -> None:
        """Sanity: a UTC day is what the budget uses for its rollover window."""
        # This isn't testing runtime behaviour so much as the API contract of
        # DailyTokenBudget — the ``now`` injection is a ``-> date`` callable.
        d1 = date(2026, 1, 1)
        d2 = d1 + timedelta(days=1)
        assert d2 != d1
