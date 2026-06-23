"""Bounded background-task runner."""

import asyncio

from rankforge_backend import tasks


async def test_spawn_runs_and_drain_waits():
    done: list[int] = []

    async def work(n: int):
        await asyncio.sleep(0)
        done.append(n)

    tasks.spawn(work(1))
    tasks.spawn(work(2))
    await tasks.drain(timeout=2)
    assert sorted(done) == [1, 2]


async def test_spawn_caps_concurrency(monkeypatch):
    monkeypatch.setattr(tasks, "_sem", asyncio.Semaphore(2))
    active = 0
    peak = 0

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1

    for _ in range(6):
        tasks.spawn(work())
    await tasks.drain(timeout=3)
    assert peak <= 2  # never more than the cap ran at once
