"""Bounded background-task runner.

Heavy pipeline work (generation, research, refine, scouts) runs as detached
asyncio tasks. Without a cap, a burst of requests — or a scout tick overlapping a
manual generation — could spawn unbounded concurrent tasks that exhaust the DB
connection pool and starve the event loop. `spawn` caps concurrency well under the
pool size (excess work queues) and keeps a reference so tasks aren't GC'd; `drain`
lets the lifespan await in-flight work on shutdown.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from .config import get_settings

_sem: asyncio.Semaphore | None = None
_tasks: set[asyncio.Task] = set()


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(get_settings().max_background_tasks)
    return _sem


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Run a coroutine as a capped background task. Over the cap, it queues."""
    sem = _semaphore()

    async def _runner() -> None:
        async with sem:
            await coro

    task = asyncio.create_task(_runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain(timeout: float = 20.0) -> None:
    """Best-effort await of in-flight tasks (graceful shutdown)."""
    if _tasks:
        await asyncio.wait(set(_tasks), timeout=timeout)
