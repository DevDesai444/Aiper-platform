"""Parsing runs in a disposable child process, never in the API.

pypdf and the OOXML parsers walk attacker-controlled structures: a crafted
file can loop forever, expand into gigabytes (decompression bombs), or kill
the interpreter outright. The API process must survive all three, so every
parse happens in a spawned child with a wall-clock deadline and — where the
OS supports it — an address-space ceiling. On any breach the child is killed
and the failure is recorded on the asset like any other indexing error; the
uploader gets a message, the API keeps serving.

Spawn, not fork: a forked child would inherit the API's sockets, connection
pools and event-loop state. A spawned interpreter imports only this module
and the loader stack, and the limits it runs under travel as arguments — the
parent reads settings, the child never does.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
from multiprocessing.connection import Connection
from pathlib import Path

from anyio.to_thread import run_sync

from app.config import settings
from app.rag.loaders import Page, ParseRejected, load_pages

logger = logging.getLogger(__name__)

_ctx = multiprocessing.get_context("spawn")

# Parsing is CPU-bound and a child may legitimately hold hundreds of MB while
# it works; more than a few at once is how one bad batch starves the host.
_slots = threading.BoundedSemaphore(settings.parse_concurrency)


class ParseTimeout(ParseRejected):
    pass


class ParseCrashed(Exception):
    """The child died without reporting — segfault, OOM kill, or similar."""


def _child(path: str, extension: str, max_pages: int, memory_mb: int, conn: Connection) -> None:
    """Runs in the spawned process. Reports exactly one message and exits."""
    try:
        try:
            import resource

            ceiling = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
        except (ImportError, ValueError, OSError):
            # macOS dev boxes ignore RLIMIT_AS; the deadline still applies.
            pass

        pages = load_pages(path, extension, max_pages=max_pages)
        conn.send(("ok", [(p.page, p.text) for p in pages]))
    except ParseRejected as exc:
        conn.send(("rejected", str(exc)))
    except MemoryError:
        conn.send(("rejected", "The file needs more memory to parse than allowed."))
    except BaseException as exc:  # noqa: BLE001 - the parent decides what to surface
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def _parse_blocking(path: str, extension: str) -> list[Page]:
    timeout = settings.parse_timeout_seconds
    if not _slots.acquire(timeout=timeout):
        raise ParseTimeout(
            "Indexing is busy; the file waited too long for a parser slot. Try again shortly."
        )
    try:
        parent_conn, child_conn = _ctx.Pipe(duplex=False)
        proc = _ctx.Process(
            target=_child,
            args=(path, extension, settings.max_pages_per_file, settings.parse_memory_mb, child_conn),
            daemon=True,
        )
        proc.start()
        # The child holds the only open write end; without this close, EOF on
        # a dead child would never arrive at the parent.
        child_conn.close()
        try:
            if not parent_conn.poll(timeout):
                raise ParseTimeout(
                    f"Parsing did not finish within {timeout}s; "
                    "the file may be malformed or too complex to index."
                )
            try:
                outcome = parent_conn.recv()
            except EOFError as exc:
                raise ParseCrashed("The parser exited before reporting a result.") from exc
        finally:
            if proc.is_alive():
                proc.kill()
            proc.join(5)
            parent_conn.close()
    finally:
        _slots.release()

    kind, payload = outcome
    if kind == "ok":
        return [Page(page=number, text=text) for number, text in payload]
    if kind == "rejected":
        raise ParseRejected(payload)
    raise ParseCrashed(payload)


async def parse_in_sandbox(path: str | Path, extension: str) -> list[Page]:
    """Parse `path` in a killable child under the configured limits."""
    return await run_sync(_parse_blocking, str(path), extension)
