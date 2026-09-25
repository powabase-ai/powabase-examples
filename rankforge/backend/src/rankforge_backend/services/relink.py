"""M6 / Phase 12.3 — monthly re-linking maintenance scout.

As the blog grows, a freshly published article should pick up links from older
posts, and older posts should link to it. This re-runs the deterministic internal-
link suggester (services.linking) across the brand's whole PUBLISHED library on a
cadence, staging suggestions for review — it never edits published content directly.

Scheduling state is durable (`relink_configs.next_run_at`); the in-process
APScheduler tick (scheduler.py) drives it, the same way it drives content scouts.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from ..db import Database
from . import business_profiles as brands
from . import linking

log = logging.getLogger("rankforge.relink")

_CONFIG_COLUMNS = (
    "business_id, enabled, cadence, last_run_at, next_run_at, last_found, updated_at"
)
# Bound a single run so a very large library can't make one tick run unbounded.
_MAX_ARTICLES_PER_RUN = 300


def _cadence_delta(cadence: str) -> timedelta:
    return timedelta(days=7) if cadence == "weekly" else timedelta(days=30)


def get_config(db: Database, business_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_CONFIG_COLUMNS} from public.relink_configs where business_id = %s",
        (business_id,),
    )


def default_config(business_id: UUID) -> dict[str, Any]:
    """A transient default for reads — does NOT persist (the row is created on PUT)."""
    return {
        "business_id": business_id,
        "enabled": False,
        "cadence": "monthly",
        "last_run_at": None,
        "next_run_at": None,
        "last_found": 0,
        "updated_at": None,
    }


def ensure_config(db: Database, business_id: UUID) -> dict[str, Any]:
    row = get_config(db, business_id)
    if row:
        return row
    return db.fetch_one(
        "insert into public.relink_configs (business_id) values (%s) "
        "on conflict (business_id) do update set business_id = excluded.business_id "
        f"returning {_CONFIG_COLUMNS}",
        (business_id,),
    )


def update_config(
    db: Database, business_id: UUID, fields: dict[str, Any]
) -> dict[str, Any]:
    ensure_config(db, business_id)
    fields = {k: v for k, v in fields.items() if v is not None}
    # (Re)schedule the next run when enabling or changing cadence.
    if fields.get("enabled") or "cadence" in fields:
        cadence = fields.get("cadence") or get_config(db, business_id)["cadence"]
        fields["next_run_at"] = datetime.now(UTC) + _cadence_delta(cadence)
    sets = [f"{k} = %s" for k in fields]
    sets.append("updated_at = now()")
    params = list(fields.values()) + [business_id]
    return db.fetch_one(
        f"update public.relink_configs set {', '.join(sets)} "
        f"where business_id = %s returning {_CONFIG_COLUMNS}",
        tuple(params),
    )


def due_configs(db: Database) -> list[dict[str, Any]]:
    """Enabled configs whose next run is due (or never scheduled)."""
    return db.fetch_all(
        f"select {_CONFIG_COLUMNS} from public.relink_configs "
        "where enabled and (next_run_at is null or next_run_at <= now())"
    )


def run_relink(db: Database, business_id: UUID) -> dict[str, Any]:
    """Re-run the internal-link suggester across every published article in the brand's
    library, staging new suggestions for review. Pure DB work (the suggester is
    deterministic — no LLM/network), so it's cheap to run library-wide. Always rolls
    the schedule forward, even on a partial failure.
    """
    found = 0
    scanned = 0
    cfg = ensure_config(db, business_id)
    try:
        published = db.fetch_all(
            "select id from public.articles "
            "where business_id = %s and status = 'published' "
            f"order by updated_at desc limit {_MAX_ARTICLES_PER_RUN}",
            (business_id,),
        )
        # Fetch the candidate link-target set ONCE and reuse it for every article, so
        # the sweep is O(N) instead of suggest_links -> _link_targets re-fetching the
        # whole published library per article (O(N²)).
        candidates = db.fetch_all(
            f"select {linking._TARGET_COLS} from public.articles "
            "where business_id = %s and status = 'published'",
            (business_id,),
        )
        for row in published:
            scanned += 1
            try:
                found += len(
                    linking.suggest_links(
                        db, business_id, row["id"], candidates=candidates
                    )
                )
            except Exception:  # noqa: BLE001 — one article shouldn't fail the whole run
                log.exception("relink: suggest failed for article %s", row["id"])
        db.execute(
            "update public.relink_configs set last_run_at = now(), last_found = %s "
            "where business_id = %s",
            (found, business_id),
        )
    finally:
        # Roll the schedule forward regardless of outcome.
        try:
            db.execute(
                "update public.relink_configs set "
                "next_run_at = now() + %s where business_id = %s",
                (_cadence_delta(cfg.get("cadence") or "monthly"), business_id),
            )
        except Exception:  # noqa: BLE001
            pass
    return {"articles_scanned": scanned, "suggestions_found": found}


def _sentence_with(md: str, anchor: str) -> str:
    """Extract the sentence from markdown that contains the anchor text."""
    for s in re.split(r"(?<=[.!?])\s+|\n+", md or ""):
        if anchor.lower() in s.lower():
            return s.strip()
    return ""


def patch_notes(db: Database, business_id: UUID) -> str:
    """Pending suggestions on PUBLISHED articles as copy-paste Markdown for editing
    the live site repo (the site, not RankForge, owns already-published posts).
    Each heading is the article's live URL."""
    brand = brands.get_profile(db, business_id)
    rows = db.fetch_all(
        "select a.id, a.slug, a.canonical_url, s.anchor_text, s.target_url, "
        "s.target_title, a.content_md "
        "from public.link_suggestions s join public.articles a on a.id = s.article_id "
        "where s.business_id = %s and s.status = 'pending' "
        "and a.status = 'published' "
        "order by a.slug, a.id, s.created_at",
        (business_id,),
    )
    if not rows:
        return "No pending link suggestions.\n"
    out: list[str] = []
    current = object()
    for r in rows:
        if r.get("id", r["slug"]) != current:
            current = r.get("id", r["slug"])
            url = linking.canonical_url(brand, r) or f"/blog/{r['slug']}/"
            out.append(f"{'' if not out else chr(10)}### {url}")
        if r.get("anchor_text"):
            sent = _sentence_with(r.get("content_md") or "", r["anchor_text"])
            out.append(f'- "{r["anchor_text"]}" → {r["target_url"]}  (in: "{sent}")')
        else:
            out.append(
                f'- (new sentence) → {r["target_url"]}  — "{r.get("target_title") or ""}"'
            )
    return "\n".join(out) + "\n"
