"""In-process scout scheduler (APScheduler).

A single recurring tick polls `scout_configs` for due brands and runs each scout
once, concurrently, guarding against overlapping runs of the same brand. Durable
scheduling state lives in the DB (`next_run_at`); the scheduler only ticks. It is
started in the app lifespan and only when a DB + Powabase client are configured,
so the hermetic test app never starts it.
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .db import Database
from .powabase import PowabaseClient
from .services import scouts as scout_svc

log = logging.getLogger("rankforge.scheduler")

TICK_SECONDS = 300


class ScoutScheduler:
    def __init__(self, db: Database, pb: PowabaseClient):
        self._db = db
        self._pb = pb
        self._sched = AsyncIOScheduler()
        self._running: set = set()

    def start(self) -> None:
        self._sched.add_job(
            self._tick, "interval", seconds=TICK_SECONDS, id="scout-tick"
        )
        self._sched.start()
        log.info("scout scheduler started (tick=%ss)", TICK_SECONDS)

    def shutdown(self) -> None:
        self._sched.shutdown(wait=False)

    async def _tick(self) -> None:
        try:
            due = scout_svc.due_configs(self._db)
        except Exception:  # noqa: BLE001
            log.exception("scout tick: failed to query due configs")
            return
        for cfg in due:
            bid = cfg["business_id"]
            if bid in self._running:
                continue
            self._running.add(bid)
            asyncio.create_task(self._run(bid))

    async def _run(self, business_id) -> None:
        try:
            await scout_svc.run_scout(
                self._pb, self._db, business_id=business_id, trigger="schedule"
            )
        except Exception:  # noqa: BLE001
            log.exception("scout run failed for %s", business_id)
        finally:
            self._running.discard(business_id)
