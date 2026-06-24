"""Startup reconciliation of interrupted background work.

Background tasks (research, generation, scout auto-draft) run in-process and do NOT
survive a restart/crash. So at startup, any row still in a non-terminal "working"
state was orphaned by a previous process and will otherwise sit stuck forever (the
UI shows an eternal "Drafting…"/"running"). A fresh process has nothing in flight,
so it's safe to reset them all: drafts go back to the inbox (retryable), and runs
in progress are marked failed.
"""

import logging

from ..db import Database

log = logging.getLogger("rankforge.reconcile")


def _count_update(db: Database, sql: str) -> int:
    row = db.fetch_one(
        f"with x as ({sql} returning 1) select count(*)::int as n from x"
    )
    return (row or {}).get("n", 0) or 0


def reconcile_interrupted(db: Database) -> None:
    """Reset rows left mid-flight by a prior restart. Idempotent; safe to run on
    every startup (single-instance deployment — nothing is in flight at boot)."""
    opp = _count_update(
        db,
        "update public.opportunities set status = 'new', updated_at = now() "
        "where status in ('queued', 'drafting')",
    )
    run = _count_update(
        db,
        "update public.research_runs set status = 'failed', "
        "error = 'interrupted by a server restart' "
        "where status in ('queued', 'searching', 'scraping', 'analyzing')",
    )
    art = _count_update(
        db,
        "update public.articles set generation_status = 'failed', "
        "generation_error = 'interrupted by a server restart' "
        "where generation_status in "
        "('grounding', 'outlining', 'drafting', 'optimizing', 'refining')",
    )
    if opp or run or art:
        log.info(
            "startup reconciliation: reset %s opportunity(ies), %s research run(s), "
            "%s article(s) orphaned by a prior restart",
            opp, run, art,
        )
