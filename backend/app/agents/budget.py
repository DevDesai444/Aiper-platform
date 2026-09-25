"""Per-user daily token budget for the agent runtime.

Counters are held in-process, keyed by (user_id, UTC-day). Two implications:

* Every worker maintains its own view. With N workers a user can, in the worst
  case, consume ~N × cap before every worker refuses. That is acceptable for
  the current single-worker demo; the shared-Redis version is a later step.
* Everything resets when the process restarts. The per-turn structured usage
  log (see ``runtime.py``) is the durable record.

The ``cap`` is read at construction time so tests can vary it; the process-wide
default (see :func:`default_budget`) reads ``settings.agent_daily_token_cap`` at
first use. A cap of ``0`` disables enforcement entirely.
"""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timezone

from app.config import settings


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


class DailyTokenBudget:
    """Thread-safe per-user daily counter.

    Not process-shared. ``check`` reports remaining budget without mutating; use
    ``record`` after a turn to add its actual usage. Callers should record even
    the usage from turns that ended in an exception, so a runaway loop still
    counts against the cap.
    """

    def __init__(self, cap: int, *, now: Callable[[], date] = _utc_today) -> None:
        self._cap = cap
        self._now = now
        self._lock = threading.Lock()
        # {user_id -> (utc_day, tokens_used_today)}
        self._state: dict[str, tuple[date, int]] = defaultdict(lambda: (self._now(), 0))

    @property
    def cap(self) -> int:
        return self._cap

    @property
    def enabled(self) -> bool:
        return self._cap > 0

    def _key(self, user_id: uuid.UUID | str | None) -> str:
        return str(user_id) if user_id is not None else "__anon__"

    def _current(self, user_id: uuid.UUID | str | None) -> tuple[date, int]:
        """Return today's (day, used) for the user, rolling over stale rows."""
        key = self._key(user_id)
        today = self._now()
        day, used = self._state.get(key, (today, 0))
        if day != today:
            day, used = today, 0
            self._state[key] = (day, used)
        return day, used

    def check(self, user_id: uuid.UUID | str | None) -> tuple[bool, int]:
        """Return ``(allowed, remaining)``. ``allowed`` is False iff over cap."""
        if not self.enabled:
            return True, 0  # remaining is unused when disabled
        with self._lock:
            _, used = self._current(user_id)
            remaining = max(0, self._cap - used)
            return remaining > 0, remaining

    def record(self, user_id: uuid.UUID | str | None, tokens: int) -> int:
        """Add ``tokens`` to today's counter for the user. Returns the new total."""
        if tokens <= 0 or not self.enabled:
            # Still return the current used value for observability.
            with self._lock:
                _, used = self._current(user_id)
                return used
        with self._lock:
            day, used = self._current(user_id)
            used += tokens
            self._state[self._key(user_id)] = (day, used)
            return used

    def snapshot(self, user_id: uuid.UUID | str | None) -> tuple[date, int]:
        """Return today's ``(day, used)`` for observability."""
        with self._lock:
            return self._current(user_id)


_default_budget: DailyTokenBudget | None = None


def default_budget() -> DailyTokenBudget:
    """Process-wide budget instance, cap taken from settings on first call."""
    global _default_budget
    if _default_budget is None:
        _default_budget = DailyTokenBudget(cap=settings.agent_daily_token_cap)
    return _default_budget


def reset_default_budget() -> None:
    """Test-only: drop the module singleton so a new one picks up fresh settings."""
    global _default_budget
    _default_budget = None
