"""A tiny in-process sliding-window rate limiter.

Used to bound brute-force attempts against the legacy login endpoint without
pulling in Redis or another shared store. State lives in this process only: it
resets on restart and is not shared across workers — acceptable because the
legacy login is a dev/demo path (single worker), not the production auth path.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Allow at most ``max_attempts`` hits per ``window_seconds`` for a given key."""

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, *, now: float | None = None) -> bool:
        """Record an attempt for ``key``.

        Returns ``True`` if the attempt is within the limit, ``False`` if the key
        has already used its full allowance inside the current window.
        """
        moment = time.monotonic() if now is None else now
        cutoff = moment - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                return False
            hits.append(moment)
            return True

    def reset(self, key: str | None = None) -> None:
        """Clear state for one key, or all keys when ``key`` is ``None``."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
