"""M5 — autonomous content scouts.

A scout discovers timely, on-brand content opportunities (Exa news/SERP +
competitor signals via a tool-using agent), scores them against the brand, and
stores them in an inbox. At the `auto_draft` autonomy level it promotes the top
opportunities through the existing generation pipeline (research → brief → draft)
and stages each result as `in_review` — it never auto-publishes.

Scheduling is owned by the in-process APScheduler tick (see `scheduler.py`); this
module is the pure worker, callable on a schedule or on a manual trigger.
"""

import logging
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
from . import clusters, generation
from . import research as research_svc
from .agents import ensure_agent

log = logging.getLogger("rankforge.scouts")

SCOUT_AGENT_NAME = "rankforge-scout"
# Discovery quality feeds auto-draft — top model + moderate extended thinking to
# weigh timeliness/fit (we still re-score deterministically in code).
SCOUT_MODEL = "claude-opus-4-7"

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
- **Never duplicate existing coverage.** You will be given the brand's already-\
published, queued, and dismissed topics under "Already covered". Do not propose any \
of them, nor a reworded variant, nor a topic that targets the same primary keyword. \
Every opportunity must be genuinely NEW — a fresh angle, sub-topic, or keyword the \
brand has not addressed.
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
    "id, business_id, status, trigger, found, drafted, error, progress, created_at"
)
_OPP_COLUMNS = (
    "id, business_id, scout_run_id, title, angle, why_now, keyword, source_type, "
    "source_url, evidence, score, scores, status, article_id, cluster_id, "
    "cluster_role, progress, created_at, updated_at"
)

async def ensure_scout_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=SCOUT_AGENT_NAME,
        model=SCOUT_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"reasoning_effort": "medium"},
        builtin_tools=("web_search",),
    )


# --- config ---
def _cadence_delta(cadence: str) -> timedelta:
    if cadence == "weekly":
        return timedelta(days=7)
    if cadence == "twice_daily":
        return timedelta(hours=12)
    return timedelta(days=1)  # daily (default)


def get_config(db: Database, business_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_CONFIG_COLUMNS} from public.scout_configs where business_id = %s",
        (business_id,),
    )


def default_config(business_id: UUID) -> dict[str, Any]:
    """A transient default for reads — does NOT persist (the row is created on PUT)."""
    return {
        "business_id": business_id,
        "enabled": False,
        "cadence": "daily",
        "autonomy": "suggest",
        "min_score": 70,
        "max_drafts_per_run": 1,
        "focus": [],
        "last_run_at": None,
        "next_run_at": None,
        "updated_at": None,
    }


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


def try_claim_opportunity(db: Database, opp_id: UUID) -> dict[str, Any] | None:
    """Atomically move an opportunity to 'queued' for drafting. Returns None if it
    is already queued/drafting/drafted (so a double-submit can't launch two draft
    pipelines for the same opportunity). The check and the flip are one statement."""
    return db.fetch_one(
        f"update public.opportunities set status = 'queued', updated_at = now() "
        f"where id = %s and status not in ('queued', 'drafting', 'drafted') "
        f"returning {_OPP_COLUMNS}",
        (opp_id,),
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


# --- existing-coverage awareness (don't re-suggest what the blog already covers) ---
_SIM_THRESHOLD = 0.7  # title-token Jaccard above which a candidate is a near-duplicate
# Bound the dedup working set to the most-recent N rows per brand so a very large
# blog can't make every scout run load (and Jaccard-scan) unbounded history. The
# most-recent N comfortably covers timely-topic collisions; older long-tail posts
# rarely collide with fresh opportunities.
_COVERAGE_LIMIT = 500


def _gather_coverage(db: Database, business_id: UUID) -> dict[str, Any]:
    """Everything the brand already covers — used both to brief the scout agent and
    to filter its candidates: published/draft articles plus open AND dismissed
    opportunities (a dismissed topic shouldn't keep coming back)."""
    articles = db.fetch_all(
        "select title, keywords, slug from public.articles "
        f"where business_id = %s order by updated_at desc limit {_COVERAGE_LIMIT}",
        (business_id,),
    )
    open_opps = db.fetch_all(
        "select title, keyword from public.opportunities "
        "where business_id = %s and status <> 'dismissed' "
        f"order by created_at desc limit {_COVERAGE_LIMIT}",
        (business_id,),
    )
    dismissed = db.fetch_all(
        "select title from public.opportunities "
        "where business_id = %s and status = 'dismissed' "
        f"order by created_at desc limit {_COVERAGE_LIMIT}",
        (business_id,),
    )
    seen = {_norm_title(r["title"]) for r in articles}
    seen |= {_norm_title(r["title"]) for r in open_opps}
    seen |= {_norm_title(r["title"]) for r in dismissed}
    # Covered primary keywords (article keywords + slug + open-opp keyword): two
    # articles targeting the same keyword is redundant for SEO.
    keywords: set[str] = set()
    for a in articles:
        for k in a.get("keywords") or []:
            if k:
                keywords.add(_norm_title(str(k)))
        if a.get("slug"):
            keywords.add(_norm_title(a["slug"].replace("-", " ")))
    for o in open_opps:
        if o.get("keyword"):
            keywords.add(_norm_title(o["keyword"]))
    token_sets = [t for r in (articles + open_opps) if (t := _tokens(r["title"]))]
    return {
        "seen": seen,
        "keywords": keywords,
        "token_sets": token_sets,
        "articles": articles,
        "opps": open_opps + dismissed,
    }


def _covers_existing(title: str, keyword: str | None, cov: dict[str, Any]) -> bool:
    """True if this candidate duplicates existing coverage: same (normalized) title,
    same primary keyword, or a high title-token overlap (a reworded variant)."""
    nt = _norm_title(title)
    if not nt or nt in cov["seen"]:
        return True
    if keyword and _norm_title(keyword) in cov["keywords"]:
        return True
    ct = _tokens(title)
    if ct:
        for ts in cov["token_sets"]:
            if len(ct & ts) / len(ct | ts) >= _SIM_THRESHOLD:
                return True
    return False


def _covered_block(cov: dict[str, Any], limit: int = 60) -> str:
    """A compact 'already covered' list for the scout prompt (most-recent first)."""
    lines: list[str] = []
    for a in cov["articles"][:limit]:
        kw = next((k for k in (a.get("keywords") or []) if k), None)
        lines.append(f'- "{a["title"]}"' + (f" — targets: {kw}" if kw else ""))
    for o in cov["opps"][: max(0, limit - len(lines))]:
        lines.append(f'- "{o["title"]}"')
    return "\n".join(lines) or "- (nothing published yet)"


# --- the worker ---
def _set_progress(db: Database, run_id: UUID, phase: str, message: str, **extra: Any):
    """Narrate what the scout is doing right now so the UI can show it live."""
    db.execute(
        "update public.scout_runs set progress = %s where id = %s",
        (Json({"phase": phase, "message": message, **extra}), run_id),
    )


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
        "insert into public.scout_runs (business_id, trigger, progress) "
        "values (%s, %s, %s) "
        f"returning {_RUN_COLUMNS}",
        (
            business_id,
            trigger,
            Json({"phase": "starting", "message": "Starting scout…"}),
        ),
    )
    run_id = run["id"]
    try:
        if brand is None:
            raise RuntimeError("brand not found")

        focus = cfg.get("focus") or brand.get("seed_topics") or []
        cov = _gather_coverage(db, business_id)
        _set_progress(
            db, run_id, "discovering",
            "Searching the web for timely, on-brand topics…",
        )
        agent_id = await ensure_scout_agent(client)
        msg = (
            "## Brand\n"
            f"- Name: {brand.get('name')}\n"
            f"- Niche: {brand.get('niche') or 'n/a'}\n"
            f"- Audience: {brand.get('audience') or 'n/a'}\n"
            f"- Focus topics: {', '.join(focus) or 'n/a'}\n"
            f"- Target keywords: {', '.join(brand.get('target_keywords') or []) or 'n/a'}\n"
            f"- Competitors: {', '.join(c.get('domain', '') for c in (brand.get('competitors') or [])) or 'n/a'}\n\n"
            "## Already covered — do NOT propose these or close variants\n"
            f"{_covered_block(cov)}\n\n"
            "## Task\n"
            "- Find 5–8 timely content opportunities for this brand right now.\n"
            "- Each MUST be genuinely new — not listed above, not a reworded variant, "
            "and not targeting a keyword the brand already covers.\n\n"
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
        _set_progress(
            db, run_id, "analyzing",
            f"Found {len(candidates)} candidate topic"
            f"{'' if len(candidates) == 1 else 's'} — filtering against your "
            "existing blog coverage…",
            considered=[c.get("title") for c in candidates if c.get("title")][:8],
        )

        brand_terms = _brand_terms(brand)

        stored: list[dict[str, Any]] = []
        for cand in candidates:
            title = (cand.get("title") or "").strip()
            keyword = cand.get("keyword")
            # Skip anything the brand already covers — exact title, same primary
            # keyword, or a paraphrase — and dedup candidates against each other.
            if _covers_existing(title, keyword, cov):
                continue
            cov["seen"].add(_norm_title(title))
            if tt := _tokens(title):
                cov["token_sets"].append(tt)
            if keyword:
                cov["keywords"].add(_norm_title(keyword))
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
        _set_progress(
            db, run_id, "scored",
            f"{len(stored)} new opportunit"
            f"{'y' if len(stored) == 1 else 'ies'} surfaced.",
        )

        # Place each opportunity into a content cluster (join an existing one or found
        # a new one) so the inbox shows topical structure and drafts inherit it. Pass
        # clusters founded earlier in THIS run as candidates to avoid intra-run dups.
        if stored:
            _set_progress(
                db, run_id, "clustering",
                "Organizing opportunities into topic clusters…",
            )
        founded: list[dict[str, Any]] = []
        for opp in stored:
            try:
                cid, role = await clusters.assign(
                    client, db, business_id,
                    title=opp["title"], keyword=opp.get("keyword"),
                    angle=opp.get("angle"), extra_candidates=founded,
                )
                db.execute(
                    "update public.opportunities set cluster_id = %s, "
                    "cluster_role = %s where id = %s",
                    (cid, role, opp["id"]),
                )
                opp["cluster_id"], opp["cluster_role"] = cid, role
                if role == "pillar" and (c := clusters.get_cluster(db, cid)):
                    founded.append(c)
            except Exception:  # noqa: BLE001 — clustering must not fail the scout run
                log.exception("cluster assignment failed for opp %s", opp["id"])

        # Auto-draft the best, on-threshold opportunities (L3).
        drafted = 0
        if cfg.get("autonomy") == "auto_draft":
            cap = cfg.get("max_drafts_per_run") or 1
            floor = cfg.get("min_score") or 70
            top = sorted(stored, key=lambda o: o["score"], reverse=True)
            for opp in top:
                if drafted >= cap or opp["score"] < floor:
                    break
                _set_progress(
                    db, run_id, "drafting",
                    f"Drafting “{opp['title']}” ({drafted + 1}/{cap})…",
                    drafted=drafted, total=cap,
                )
                ok = await auto_draft(client, db, opp)
                if ok:
                    drafted += 1

        _set_progress(
            db, run_id, "done",
            f"Done — {len(stored)} new opportunit"
            f"{'y' if len(stored) == 1 else 'ies'}"
            f"{f', {drafted} drafted' if drafted else ''}.",
        )
        db.execute(
            "update public.scout_runs set status = 'done', drafted = %s where id = %s",
            (drafted, run_id),
        )
    except Exception:  # noqa: BLE001 — record on the run row
        log.exception("scout run %s failed for business %s", run_id, business_id)
        db.execute(
            "update public.scout_runs set status = 'failed', error = %s where id = %s",
            ("scout run failed — see server logs", run_id),
        )
        _set_progress(db, run_id, "failed", "Scout run failed — see server logs.")
    finally:
        # Roll the schedule forward regardless of outcome (guarded so a failure
        # here can't mask the original error or stop the run from returning).
        try:
            db.execute(
                "update public.scout_configs set last_run_at = now(), "
                "next_run_at = now() + %s where business_id = %s",
                (_cadence_delta(cfg.get("cadence") or "daily"), business_id),
            )
        except Exception:  # noqa: BLE001
            pass
    return get_run(db, run_id)


def _set_opp_progress(db: Database, opp_id: UUID, phase: str, message: str) -> None:
    """Narrate what an auto-draft is doing right now (shown on the inbox card)."""
    db.execute(
        "update public.opportunities set progress = %s, updated_at = now() "
        "where id = %s",
        (Json({"phase": phase, "message": message}), opp_id),
    )


async def auto_draft(
    client: PowabaseClient, db: Database, opp: dict[str, Any]
) -> bool:
    """Promote one opportunity through research → brief → draft, staged in_review."""
    opp_id = opp["id"]
    business_id = opp["business_id"]
    # Research the opportunity's actual TITLE (the specific angle), not the bare
    # keyword — researching just the keyword pulls in whatever generic topic ranks
    # for it and the article drifts off the angle.
    topic = opp.get("title") or opp.get("keyword")
    set_opportunity_status(db, opp_id, "drafting")
    _set_opp_progress(
        db, opp_id, "researching",
        "Researching the topic — competitors & the search landscape…",
    )
    try:
        brand = brands.get_profile(db, business_id)
        rrun = research_svc.create_research_run(
            db, business_id=business_id, topic=topic, locale="en-US"
        )
        await research_svc.run_research_task(
            client, db, run_id=rrun["id"], brand=brand, topic=topic,
            locale="en-US", depth="standard",
        )
        # run_research_task swallows its own errors onto the run row — gate the
        # chain on research actually succeeding and producing sources, so we never
        # draft an ungrounded article from a failed/empty run.
        run = research_svc.get_run(db, rrun["id"])
        if not run or run.get("status") == "failed":
            set_opportunity_status(db, opp_id, "new")
            return False
        if not research_svc.list_sources(db, rrun["id"]):
            set_opportunity_status(db, opp_id, "new")
            return False
        _set_opp_progress(
            db, opp_id, "briefing", "Building the content brief from the research…"
        )
        brief = await brief_svc.generate_brief(
            client, db, research_run_id=rrun["id"],
            # carry the opportunity's angle into the brief so the article executes
            # it (the keyword is for SEO, not the topic).
            editorial_direction={
                "title": opp.get("title"),
                "angle": opp.get("angle"),
                "keyword": opp.get("keyword"),
            },
        )
        article = generation.create_article(db, brief)
        # The article inherits the opportunity's cluster + role (pillar/member) — this
        # is also what claims the cluster's permanent pillar slot when role='pillar'.
        if opp.get("cluster_id"):
            clusters.attach_article(
                db, article["id"], opp["cluster_id"], opp.get("cluster_role") or "member"
            )
        # Link the article now (still "drafting") so the user can open it and watch
        # the detailed generation progress while it writes.
        set_opportunity_status(db, opp_id, "drafting", article_id=article["id"])
        _set_opp_progress(
            db, opp_id, "writing",
            "Writing the grounded draft — open it to watch live…",
        )
        await generation.run_generation_task(
            client, db, article_id=article["id"], brief=brief
        )
        # Stage for human review — scouts never auto-publish.
        generation.update_article(db, article["id"], {"status": "in_review"})
        set_opportunity_status(db, opp_id, "drafted", article_id=article["id"])
        return True
    except Exception:  # noqa: BLE001 — leave the opportunity for manual retry
        log.exception("auto_draft failed for opportunity %s", opp_id)
        set_opportunity_status(db, opp_id, "new")
        return False


def get_run(db: Database, run_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_RUN_COLUMNS} from public.scout_runs where id = %s", (run_id,)
    )
