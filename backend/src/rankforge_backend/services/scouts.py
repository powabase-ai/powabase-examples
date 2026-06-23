"""M5 — autonomous content scouts.

A scout discovers timely, on-brand content opportunities (Exa news/SERP +
competitor signals via a tool-using agent), scores them against the brand, and
stores them in an inbox. At the `auto_draft` autonomy level it promotes the top
opportunities through the existing generation pipeline (research → brief → draft)
and stages each result as `in_review` — it never auto-publishes.

Scheduling is owned by the in-process APScheduler tick (see `scheduler.py`); this
module is the pure worker, callable on a schedule or on a manual trigger.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..powabase import PowabaseClient
from ..util import extract_json
from . import brief as brief_svc
from . import business_profiles as brands
from . import generation
from . import research as research_svc
from .agents import ensure_agent

SCOUT_AGENT_NAME = "rankforge-scout"
SCOUT_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """\
You are RankForge's **content scout**. Given a brand, you use the `web_search` (Exa) \
tool to find timely, high-potential blog opportunities and return them as JSON.

## What to look for
- Recent news, fresh search demand, and emerging trends relevant to the brand's \
niche and keywords.
- Gaps where competitors have covered a topic poorly or not at all.

## Selection rules
- Favor specific, actionable angles over evergreen restatements of well-covered topics.
- Prefer timely opportunities — something changed recently that makes this worth \
writing now.
- Base every opportunity on a real search result; never fabricate sources or trends.

## For each opportunity provide
- **title** — a clear working title — and **angle** — the recommended take.
- **why_now** — the timeliness rationale.
- **keyword** — the primary keyword.
- **source_type** — the signal: `news`, `serp`, or `competitor`.
- **source_url** — a supporting URL.
- **opportunity_score** — 0–100, blending timeliness, search potential, and brand fit.

## Output
- Your final message must be exactly one JSON object in a single ```json fenced \
block, with nothing after it.
"""

_SCHEMA_HINT = """{
  "opportunities": [
    {
      "title": "...",
      "angle": "...",
      "why_now": "...",
      "keyword": "...",
      "source_type": "news|serp|competitor",
      "source_url": "https://...",
      "opportunity_score": 0
    }
  ]
}"""

_CONFIG_COLUMNS = (
    "business_id, enabled, cadence, autonomy, min_score, max_drafts_per_run, "
    "focus, last_run_at, next_run_at, updated_at"
)
_RUN_COLUMNS = (
    "id, business_id, status, trigger, found, drafted, error, created_at"
)
_OPP_COLUMNS = (
    "id, business_id, scout_run_id, title, angle, why_now, keyword, source_type, "
    "source_url, evidence, score, scores, status, article_id, created_at, updated_at"
)

async def ensure_scout_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=SCOUT_AGENT_NAME,
        model=SCOUT_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"temperature": 0.2},
        builtin_tools=("web_search",),
    )


# --- config ---
def _cadence_delta(cadence: str) -> timedelta:
    return timedelta(days=7) if cadence == "weekly" else timedelta(days=1)


def get_config(db: Database, business_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_CONFIG_COLUMNS} from public.scout_configs where business_id = %s",
        (business_id,),
    )


def ensure_config(db: Database, business_id: UUID) -> dict[str, Any]:
    row = get_config(db, business_id)
    if row:
        return row
    return db.fetch_one(
        f"insert into public.scout_configs (business_id) values (%s) "
        f"on conflict (business_id) do update set business_id = excluded.business_id "
        f"returning {_CONFIG_COLUMNS}",
        (business_id,),
    )


def update_config(
    db: Database, business_id: UUID, fields: dict[str, Any]
) -> dict[str, Any]:
    ensure_config(db, business_id)
    fields = {k: v for k, v in fields.items() if v is not None}
    # Recompute the next run when enabling or changing cadence.
    if fields.get("enabled") or "cadence" in fields:
        cadence = fields.get("cadence") or get_config(db, business_id)["cadence"]
        fields["next_run_at"] = datetime.now(UTC) + _cadence_delta(cadence)
    sets = [f"{k} = %s" for k in fields]
    sets.append("updated_at = now()")
    params = [
        Json(v) if k == "focus" else v for k, v in fields.items()
    ] + [business_id]
    return db.fetch_one(
        f"update public.scout_configs set {', '.join(sets)} "
        f"where business_id = %s returning {_CONFIG_COLUMNS}",
        tuple(params),
    )


def due_configs(db: Database) -> list[dict[str, Any]]:
    """Enabled configs whose next run is due (or never scheduled)."""
    return db.fetch_all(
        f"select {_CONFIG_COLUMNS} from public.scout_configs "
        "where enabled and (next_run_at is null or next_run_at <= now())"
    )


# --- runs + opportunities (reads) ---
def list_runs(db: Database, business_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_RUN_COLUMNS} from public.scout_runs "
        "where business_id = %s order by created_at desc limit %s",
        (business_id, limit),
    )


def list_opportunities(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_OPP_COLUMNS} from public.opportunities "
        "where business_id = %s order by "
        "(status = 'dismissed'), score desc, created_at desc",
        (business_id,),
    )


def get_opportunity(db: Database, opp_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_OPP_COLUMNS} from public.opportunities where id = %s", (opp_id,)
    )


def set_opportunity_status(
    db: Database, opp_id: UUID, status: str, article_id: UUID | None = None
) -> dict[str, Any] | None:
    return db.fetch_one(
        f"update public.opportunities set status = %s, "
        "article_id = coalesce(%s, article_id), updated_at = now() "
        f"where id = %s returning {_OPP_COLUMNS}",
        (status, article_id, opp_id),
    )


# --- scoring helpers ---
_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 2}


def _brand_terms(brand: dict[str, Any]) -> set[str]:
    parts = [brand.get("niche") or "", brand.get("audience") or ""]
    parts += brand.get("seed_topics") or []
    parts += brand.get("target_keywords") or []
    return _tokens(" ".join(parts))


def _norm_title(title: str) -> str:
    return " ".join(_WORD.findall((title or "").lower()))


def score_candidate(
    cand: dict[str, Any], brand_terms: set[str]
) -> tuple[int, dict[str, Any]]:
    """Blend the agent's opportunity score with brand relevance."""
    raw = cand.get("opportunity_score")
    agent_score = max(0, min(100, int(raw))) if isinstance(raw, (int, float)) else 50
    cand_terms = _tokens(f"{cand.get('title', '')} {cand.get('keyword', '')}")
    overlap = len(cand_terms & brand_terms)
    relevance = min(1.0, overlap / 3.0)
    final = round(agent_score * (0.5 + 0.5 * relevance))
    return final, {
        "agent_score": agent_score,
        "relevance": round(relevance, 2),
        "overlap_terms": overlap,
    }


# --- the worker ---
async def run_scout(
    client: PowabaseClient,
    db: Database,
    *,
    business_id: UUID,
    trigger: str = "schedule",
) -> dict[str, Any]:
    """Discover + score opportunities; auto-draft the best ones at L3."""
    cfg = ensure_config(db, business_id)
    brand = brands.get_profile(db, business_id)
    run = db.fetch_one(
        "insert into public.scout_runs (business_id, trigger) values (%s, %s) "
        f"returning {_RUN_COLUMNS}",
        (business_id, trigger),
    )
    run_id = run["id"]
    try:
        if brand is None:
            raise RuntimeError("brand not found")

        focus = cfg.get("focus") or brand.get("seed_topics") or []
        agent_id = await ensure_scout_agent(client)
        msg = (
            "## Brand\n"
            f"- Name: {brand.get('name')}\n"
            f"- Niche: {brand.get('niche') or 'n/a'}\n"
            f"- Audience: {brand.get('audience') or 'n/a'}\n"
            f"- Focus topics: {', '.join(focus) or 'n/a'}\n"
            f"- Target keywords: {', '.join(brand.get('target_keywords') or []) or 'n/a'}\n"
            f"- Competitors: {', '.join(c.get('domain', '') for c in (brand.get('competitors') or [])) or 'n/a'}\n\n"
            "## Task\n"
            "- Find 5–8 timely content opportunities for this brand right now.\n\n"
            "## Output\n"
            "- Output ONLY a single ```json block matching this shape:\n"
            f"{_SCHEMA_HINT}"
        )
        agent_run = await client.run_agent_collect(agent_id, msg)
        if agent_run["error"]:
            raise RuntimeError(f"scout search failed: {agent_run['error']}")
        data = extract_json(agent_run["content"])
        candidates = data.get("opportunities") if isinstance(data, dict) else None
        candidates = candidates or []

        brand_terms = _brand_terms(brand)
        seen = {
            _norm_title(o["title"])
            for o in db.fetch_all(
                "select title from public.opportunities where business_id = %s "
                "and status <> 'dismissed'",
                (business_id,),
            )
        }
        seen |= {
            _norm_title(a["title"])
            for a in db.fetch_all(
                "select title from public.articles where business_id = %s",
                (business_id,),
            )
        }

        stored: list[dict[str, Any]] = []
        for cand in candidates:
            title = (cand.get("title") or "").strip()
            if not title or _norm_title(title) in seen:
                continue
            seen.add(_norm_title(title))
            score, breakdown = score_candidate(cand, brand_terms)
            if score < 40:  # not worth surfacing
                continue
            row = db.fetch_one(
                "insert into public.opportunities "
                "(business_id, scout_run_id, title, angle, why_now, keyword, "
                " source_type, source_url, evidence, score, scores) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"returning {_OPP_COLUMNS}",
                (
                    business_id, run_id, title, cand.get("angle"),
                    cand.get("why_now"), cand.get("keyword"),
                    cand.get("source_type"), cand.get("source_url"),
                    Json(cand), score, Json(breakdown),
                ),
            )
            stored.append(row)

        db.execute(
            "update public.scout_runs set found = %s where id = %s",
            (len(stored), run_id),
        )

        # Auto-draft the best, on-threshold opportunities (L3).
        drafted = 0
        if cfg.get("autonomy") == "auto_draft":
            cap = cfg.get("max_drafts_per_run") or 1
            floor = cfg.get("min_score") or 70
            top = sorted(stored, key=lambda o: o["score"], reverse=True)
            for opp in top:
                if drafted >= cap or opp["score"] < floor:
                    break
                ok = await auto_draft(client, db, opp)
                if ok:
                    drafted += 1

        db.execute(
            "update public.scout_runs set status = 'done', drafted = %s where id = %s",
            (drafted, run_id),
        )
    except Exception as e:  # noqa: BLE001 — record on the run row
        db.execute(
            "update public.scout_runs set status = 'failed', error = %s where id = %s",
            (str(e), run_id),
        )
    finally:
        # Roll the schedule forward regardless of outcome.
        db.execute(
            "update public.scout_configs set last_run_at = now(), "
            "next_run_at = now() + %s where business_id = %s",
            (_cadence_delta(cfg.get("cadence") or "daily"), business_id),
        )
    return get_run(db, run_id)


async def auto_draft(
    client: PowabaseClient, db: Database, opp: dict[str, Any]
) -> bool:
    """Promote one opportunity through research → brief → draft, staged in_review."""
    opp_id = opp["id"]
    business_id = opp["business_id"]
    topic = opp.get("keyword") or opp.get("title")
    set_opportunity_status(db, opp_id, "drafting")
    try:
        brand = brands.get_profile(db, business_id)
        rrun = research_svc.create_research_run(
            db, business_id=business_id, topic=topic, locale="en-US"
        )
        await research_svc.run_research_task(
            client, db, run_id=rrun["id"], brand=brand, topic=topic,
            locale="en-US", depth="standard",
        )
        brief = await brief_svc.generate_brief(
            client, db, research_run_id=rrun["id"]
        )
        article = generation.create_article(db, brief)
        await generation.run_generation_task(
            client, db, article_id=article["id"], brief=brief
        )
        # Stage for human review — scouts never auto-publish.
        generation.update_article(db, article["id"], {"status": "in_review"})
        set_opportunity_status(db, opp_id, "drafted", article_id=article["id"])
        return True
    except Exception:  # noqa: BLE001 — leave the opportunity for manual retry
        set_opportunity_status(db, opp_id, "new")
        return False


def get_run(db: Database, run_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_RUN_COLUMNS} from public.scout_runs where id = %s", (run_id,)
    )
