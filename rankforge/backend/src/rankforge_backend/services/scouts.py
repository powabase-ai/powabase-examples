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
from collections.abc import Iterator
from contextlib import contextmanager
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
You are RankForge's content scout **executor**. You're given a Search Plan — specific \
queries to run with the `web_search` (Exa) tool, each tagged with a source type — plus \
the brand and what it already covers. Run the searches and return the best NEW, timely \
blog opportunities as JSON.

## Honor each query's source tag
- **news** — prefer reputable outlets / trade press; favor the last ~30 days (news \
updates far more often than static blogs, so it's your freshest signal).
- **youtube** — find well-regarded tutorials/explainers (add "tutorial" or \
`site:youtube.com`); favor videos with real engagement and note the channel.
- **social** — surface high-engagement posts from credible accounts on trusted \
platforms (X, LinkedIn, Reddit).
- **web** — high-authority general pages.

## Selection rules
- Favor specific, actionable, TIMELY angles — something changed recently.
- **Never duplicate existing coverage** (given under "Already covered"), nor a \
reworded variant, nor a topic targeting the same primary keyword.
- Base every opportunity on a REAL search result; never fabricate sources or trends.

## For each opportunity provide
- **title** and **angle** (the recommended take).
- **why_now** — the timeliness rationale.
- **keyword** — the primary keyword.
- **source_type** — `news`, `youtube`, `social`, `serp`, or `competitor`.
- **source_url** — the backing result.
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
      "source_type": "news|youtube|social|serp|competitor",
      "source_url": "https://...",
      "opportunity_score": 0
    }
  ]
}"""

# The plan step samples what's trending and proposes varied searches across sources.
_SOURCES = ("news", "youtube", "social", "web")
PLANNER_AGENT_NAME = "rankforge-scout-planner"
PLANNER_MODEL = "claude-sonnet-4-6"
_PLANNER_SYSTEM = """\
You are RankForge's scout **planner**. BEFORE hunting for content opportunities, you \
research what's HOT in the brand's niche RIGHT NOW and propose a focused, VARIED set of \
web searches to run next. Use the `web_search` (Exa) tool to sample recent activity.

## Goal
Produce a diverse Search Plan so each run explores FRESH ground — not the same \
evergreen seed terms every time. Bias hard toward what changed recently.

## Spread queries across these sources (vary them — don't pile onto one)
- **news** — reputable outlets / trade press (freshest signal).
- **youtube** — tutorials & explainers (how-to demand, what creators are covering).
- **social** — high-engagement posts from credible accounts (X, LinkedIn, Reddit).
- **web** — high-authority general pages.

## Rules
- 5–8 queries, each tied to a SPECIFIC trending angle or question — not the brand's \
generic seed terms.
- Steer away from topics already covered (you'll be given the list).
- Give each query a one-line rationale (why it's timely / promising).

## Output
- Your final message must be exactly one JSON object in a single ```json fenced \
block, with nothing after it.
"""

_PLAN_SCHEMA_HINT = """{
  "themes": ["a short trending theme", "..."],
  "queries": [
    {
      "query": "the exact web search to run",
      "source": "news|youtube|social|web",
      "rationale": "why this is timely / promising"
    }
  ]
}"""

_CONFIG_COLUMNS = (
    "business_id, enabled, cadence, autonomy, min_score, max_drafts_per_run, "
    "focus, last_run_at, next_run_at, updated_at"
)
_RUN_COLUMNS = (
    "id, business_id, status, trigger, found, drafted, error, progress, plan, "
    "created_at"
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


async def ensure_planner_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=PLANNER_AGENT_NAME,
        model=PLANNER_MODEL,
        system_prompt=_PLANNER_SYSTEM,
        settings={"reasoning_effort": "low"},
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


def delete_opportunity(db: Database, opp_id: UUID) -> bool:
    """Permanently remove an opportunity (vs dismiss, which keeps it for restore).
    App-side only — opportunities own no Powabase resource.

    The status guard lives IN the statement (not just the route's pre-check) so a
    concurrent draft claiming the opp to 'queued'/'drafting' in the TOCTOU window
    can't have its row deleted out from under the in-flight pipeline — which would
    orphan the article and silently no-op auto_draft's later status flip. Returns
    False (the caller 409s) when the opp is mid-draft and so was not deleted."""
    return (
        db.fetch_one(
            "delete from public.opportunities "
            "where id = %s and status not in ('queued', 'drafting') returning id",
            (opp_id,),
        )
        is not None
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
# Title-token Jaccard above which a candidate is treated as a near-duplicate of
# existing coverage. Calibration tradeoff: too high lets reworded variants ('X tips'
# vs 'X guide', Jaccard ~0.6) through as fresh, too low false-merges genuinely
# distinct angles. 0.78 keeps close rewordings out while leaving room for distinct
# takes; the exact-title and shared-keyword filters still catch the obvious cases.
_SIM_THRESHOLD = 0.78
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
        # Only OPEN opps go in the prompt's "already covered" list. Dismissed titles
        # stay in `seen` (exact-match filter) so we don't re-surface the exact same
        # topic, but we no longer over-constrain the agent away from fresh angles on a
        # theme the user previously passed on.
        "opps": open_opps,
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


# --- cluster gap analysis: a pillar's subtopics with no dedicated member yet ---
# Headings that aren't real subtopics — skip them as opportunity seeds.
_GENERIC_HEADING = re.compile(
    r"(?i)^(introduction|intro|conclusion|summary|overview|faq|frequently asked|"
    r"getting started|wrap[- ]?up|final thoughts|key takeaways?|takeaways?|"
    r"what is|why|tl;?dr)\b"
)


def _pillar_subtopics(
    pillar: dict[str, Any], brief: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Candidate subtopics a pillar implies — its brief's secondary keywords plus its
    own H2 sections — each a possible standalone member article. Generic headings
    (intro/conclusion/…) are dropped."""
    cands: list[tuple[str, str | None]] = []
    if brief:
        cands += [(k, k) for k in (brief.get("secondary_keywords") or []) if k]
    for m in re.finditer(r"(?m)^##[ \t]+(.+?)\s*$", pillar.get("content_md") or ""):
        cands.append((m.group(1).strip(), None))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, kw in cands:
        label = (label or "").strip()
        nt = _norm_title(label)
        if len(label) > 3 and nt and nt not in seen and not _GENERIC_HEADING.match(label):
            seen.add(nt)
            out.append({"label": label, "keyword": kw})
    return out[:12]


def analyze_cluster_gaps(db: Database, business_id: UUID, cluster_id: UUID) -> int:
    """Stage opportunities for the pillar subtopics this cluster doesn't yet cover with
    a dedicated article. Deterministic; dedups against all existing brand coverage."""
    cl = clusters.get_cluster(db, cluster_id)
    if not cl or not cl.get("pillar_article_id"):
        return 0
    pillar = generation.get_article(db, cl["pillar_article_id"])
    if not pillar:
        return 0
    brief = (
        brief_svc.get_brief(db, pillar["brief_id"]) if pillar.get("brief_id") else None
    )
    subtopics = _pillar_subtopics(pillar, brief)
    if not subtopics:
        return 0
    cov = _gather_coverage(db, business_id)
    label = cl.get("label") or "this"
    created = 0
    for st in subtopics:
        if _covers_existing(st["label"], st.get("keyword"), cov):
            continue
        db.execute(
            "insert into public.opportunities "
            "(business_id, title, angle, keyword, source_type, evidence, score, "
            " scores, status, cluster_id, cluster_role) "
            "values (%s, %s, %s, %s, 'gap', %s, %s, %s, 'new', %s, 'member')",
            (
                business_id, st["label"],
                f'Deepen the "{label}" cluster with a dedicated article on this '
                "subtopic, linking up to the pillar.",
                st.get("keyword"), Json({}), 60, Json({}), cluster_id,
            ),
        )
        cov["seen"].add(_norm_title(st["label"]))  # avoid intra-run duplicates
        created += 1
    return created


def analyze_all_gaps(db: Database, business_id: UUID, *, budget: int = 20) -> int:
    """Run gap analysis across all of a brand's clusters (maintenance pass). Capped at
    `budget` new opportunities so the first run can't flood the inbox; subsequent runs
    dedup, so the remaining gaps surface over later passes."""
    total = 0
    for cl in clusters.list_clusters(db, business_id):
        if total >= budget:
            break
        try:
            total += analyze_cluster_gaps(db, business_id, cl["id"])
        except Exception:  # noqa: BLE001 — one cluster shouldn't fail the sweep
            log.exception("gap analysis failed for cluster %s", cl["id"])
    return total


# --- the worker ---
def _set_progress(db: Database, run_id: UUID, phase: str, message: str, **extra: Any):
    """Narrate what the scout is doing right now so the UI can show it live."""
    db.execute(
        "update public.scout_runs set progress = %s where id = %s",
        (Json({"phase": phase, "message": message, **extra}), run_id),
    )


# --- the Search Plan (trending queries the user can review/edit) ---
def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a plan (agent-generated or user-edited): clean query list, valid
    sources, bounded lengths."""
    queries: list[dict[str, Any]] = []
    for q in plan.get("queries") or []:
        if not isinstance(q, dict):
            continue
        query = (q.get("query") or "").strip()
        if not query:
            continue
        src = q.get("source")
        queries.append({
            "query": query[:200],
            "source": src if src in _SOURCES else "web",
            "rationale": (q.get("rationale") or "").strip()[:240],
        })
        if len(queries) >= 12:
            break
    themes = [str(t).strip() for t in (plan.get("themes") or []) if str(t).strip()][:8]
    return {"themes": themes, "queries": queries}


def _plan_block(plan: dict[str, Any]) -> str:
    lines: list[str] = []
    for q in plan.get("queries") or []:
        src = q.get("source") or "web"
        r = (q.get("rationale") or "").strip()
        lines.append(f'- [{src}] "{q.get("query")}"' + (f" — {r}" if r else ""))
    return "\n".join(lines) or (
        "- (no queries — search broadly for timely, on-brand topics across "
        "news/youtube/social/web)"
    )


def _brand_block(brand: dict[str, Any], focus: list[str]) -> str:
    return (
        "## Brand\n"
        f"- Name: {brand.get('name')}\n"
        f"- Niche: {brand.get('niche') or 'n/a'}\n"
        f"- Audience: {brand.get('audience') or 'n/a'}\n"
        f"- Focus topics: {', '.join(focus) or 'n/a'}\n"
        f"- Target keywords: {', '.join(brand.get('target_keywords') or []) or 'n/a'}\n"
        "- Competitors: "
        f"{', '.join(c.get('domain', '') for c in (brand.get('competitors') or [])) or 'n/a'}"
    )


async def _generate_plan(
    client: PowabaseClient, brand: dict[str, Any], focus: list[str], cov: dict[str, Any]
) -> dict[str, Any]:
    """Sample what's trending and propose a varied Search Plan across sources."""
    agent_id = await ensure_planner_agent(client)
    msg = (
        f"{_brand_block(brand, focus)}\n\n"
        "## Already covered — steer AWAY from these\n"
        f"{_covered_block(cov)}\n\n"
        "## Task\n"
        "- Research what's trending in this niche right now and propose 5–8 varied, "
        "timely search queries spread across news / youtube / social / web.\n\n"
        "## Output\n"
        f"- Output ONLY a single ```json block matching this shape:\n{_PLAN_SCHEMA_HINT}"
    )
    res = await client.run_agent_collect(agent_id, msg)
    if res.get("error"):
        raise RuntimeError(f"scout planning failed: {res['error']}")
    data = extract_json(res["content"])
    return _normalize_plan(data if isinstance(data, dict) else {})


async def _run_executor(
    client: PowabaseClient,
    brand: dict[str, Any],
    focus: list[str],
    cov: dict[str, Any],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run the plan's queries via the scout agent and return raw candidates."""
    agent_id = await ensure_scout_agent(client)
    msg = (
        f"{_brand_block(brand, focus)}\n\n"
        "## Search plan — run THESE queries with web_search (honor each source tag)\n"
        f"{_plan_block(plan)}\n\n"
        "## Already covered — do NOT propose these or close variants\n"
        f"{_covered_block(cov)}\n\n"
        "## Task\n"
        "- Run the planned searches and return 5–10 genuinely new, timely "
        "opportunities, each tied to a real result.\n\n"
        "## Output\n"
        f"- Output ONLY a single ```json block matching this shape:\n{_SCHEMA_HINT}"
    )
    res = await client.run_agent_collect(agent_id, msg)
    if res.get("error"):
        raise RuntimeError(f"scout search failed: {res['error']}")
    data = extract_json(res["content"])
    cands = data.get("opportunities") if isinstance(data, dict) else None
    return cands or []


def _roll_schedule(db: Database, business_id: UUID, cadence: str | None) -> None:
    """Push the next scheduled run forward (guarded — never masks the real outcome)."""
    try:
        db.execute(
            "update public.scout_configs set last_run_at = now(), "
            "next_run_at = now() + %s where business_id = %s",
            (_cadence_delta(cadence or "daily"), business_id),
        )
    except Exception:  # noqa: BLE001
        pass


async def _discover_and_store(
    client: PowabaseClient,
    db: Database,
    run_id: UUID,
    business_id: UUID,
    brand: dict[str, Any],
    cfg: dict[str, Any],
    plan: dict[str, Any],
    cov: dict[str, Any] | None = None,
) -> None:
    """Run the plan, store + cluster the opportunities, optionally auto-draft, and mark
    the run done. Raises on failure (the caller records 'failed').

    `cov` lets the one-shot path hand over the coverage snapshot it already built for
    planning, halving the (up to 3 queries x 500 rows) coverage scan on that path."""
    focus = cfg.get("focus") or brand.get("seed_topics") or []
    cov = cov if cov is not None else _gather_coverage(db, business_id)
    _set_progress(
        db, run_id, "discovering",
        "Running your search plan across news, YouTube, social & the web…",
    )
    candidates = await _run_executor(client, brand, focus, cov, plan)
    _set_progress(
        db, run_id, "analyzing",
        f"Found {len(candidates)} candidate topic"
        f"{'' if len(candidates) == 1 else 's'} — filtering against your existing "
        "blog coverage…",
        considered=[c.get("title") for c in candidates if c.get("title")][:8],
    )

    brand_terms = _brand_terms(brand)
    stored: list[dict[str, Any]] = []
    for cand in candidates:
        title = (cand.get("title") or "").strip()
        keyword = cand.get("keyword")
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

    # Cluster each opportunity (join an existing cluster or found a new one) so the
    # inbox shows topical structure and drafts inherit it.
    if stored:
        _set_progress(
            db, run_id, "clustering", "Organizing opportunities into topic clusters…"
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
        for opp in sorted(stored, key=lambda o: o["score"], reverse=True):
            if drafted >= cap or opp["score"] < floor:
                break
            _set_progress(
                db, run_id, "drafting",
                f"Drafting “{opp['title']}” ({drafted + 1}/{cap})…",
                drafted=drafted, total=cap,
            )
            if await auto_draft(client, db, opp):
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


def start_plan(
    db: Database, business_id: UUID, *, trigger: str = "manual"
) -> dict[str, Any]:
    """Create the 'planned' run row synchronously (so the route can return it); the
    plan itself is filled in by generate_plan_for_run (spawned)."""
    ensure_config(db, business_id)
    # Drop any earlier plan the user never ran — one open plan per brand, and stale
    # 'planned' rows don't pile up or masquerade as the latest run. (They carry no
    # opportunities, so this is a clean delete.)
    db.execute(
        "delete from public.scout_runs where business_id = %s and status = 'planned'",
        (business_id,),
    )
    return db.fetch_one(
        "insert into public.scout_runs (business_id, status, trigger, progress) "
        "values (%s, 'planned', %s, %s) "
        f"returning {_RUN_COLUMNS}",
        (
            business_id, trigger,
            Json({"phase": "planning", "message": "Researching what's trending…"}),
        ),
    )


async def generate_plan_for_run(
    client: PowabaseClient, db: Database, run_id: UUID
) -> None:
    """Fill a planned run's Search Plan (the slow LLM+web_search step). Spawned after
    start_plan; the UI polls the run until the plan appears."""
    run = get_run(db, run_id)
    if run is None:
        return
    business_id = run["business_id"]
    cfg = ensure_config(db, business_id)
    brand = brands.get_profile(db, business_id)
    try:
        if brand is None:
            raise RuntimeError("brand not found")
        focus = cfg.get("focus") or brand.get("seed_topics") or []
        plan = await _generate_plan(
            client, brand, focus, _gather_coverage(db, business_id)
        )
        db.execute(
            "update public.scout_runs set plan = %s where id = %s",
            (Json(plan), run_id),
        )
        _set_progress(
            db, run_id, "planned", "Search plan ready — review, tweak, and run it."
        )
    except Exception:  # noqa: BLE001 — record on the run row
        log.exception("scout planning %s failed for business %s", run_id, business_id)
        db.execute(
            "update public.scout_runs set status = 'failed', error = %s where id = %s",
            ("scout planning failed — see server logs", run_id),
        )
        _set_progress(db, run_id, "failed", "Couldn't build a search plan — see logs.")


def update_plan(
    db: Database, run_id: UUID, plan: dict[str, Any]
) -> dict[str, Any] | None:
    """Replace a planned run's Search Plan with the user's edits (only while it's still
    in the 'planned' state — once it executes, the plan is locked)."""
    return db.fetch_one(
        "update public.scout_runs set plan = %s "
        f"where id = %s and status = 'planned' returning {_RUN_COLUMNS}",
        (Json(_normalize_plan(plan)), run_id),
    )


async def execute_run(
    client: PowabaseClient, db: Database, run_id: UUID
) -> dict[str, Any] | None:
    """Execute a previously-planned run (the user reviewed/edited its plan). No-op if
    the run isn't in the 'planned' state (already running/done)."""
    run = get_run(db, run_id)
    if run is None:
        return None
    if run["status"] != "planned":
        return run
    # Atomically CLAIM the run (compare-and-set on the same statement) so a double
    # "Run" — or a retried execute — can't run the plan twice (duplicate Exa spend,
    # duplicate opportunities/drafts). If we don't win the flip, someone else started it.
    claimed = db.fetch_one(
        "update public.scout_runs set status = 'running' "
        "where id = %s and status = 'planned' returning id",
        (run_id,),
    )
    if claimed is None:
        return get_run(db, run_id)
    business_id = run["business_id"]
    cfg = ensure_config(db, business_id)
    brand = brands.get_profile(db, business_id)
    try:
        if brand is None:
            raise RuntimeError("brand not found")
        await _discover_and_store(
            client, db, run_id, business_id, brand, cfg, run.get("plan") or {}
        )
    except Exception:  # noqa: BLE001
        log.exception("scout run %s failed for business %s", run_id, business_id)
        db.execute(
            "update public.scout_runs set status = 'failed', error = %s where id = %s",
            ("scout run failed — see server logs", run_id),
        )
        _set_progress(db, run_id, "failed", "Scout run failed — see server logs.")
    # No _roll_schedule here: the two-phase plan→execute flow is manual-only
    # (start_plan always inserts trigger='manual'), and a manual run never perturbs
    # the brand's automatic cadence. Only the scheduled run_scout path rolls next_run_at.
    return get_run(db, run_id)


@contextmanager
def _brand_run_lock(db: Database, business_id: UUID) -> Iterator[bool]:
    """Serialize whole scout runs for one brand. The scheduler's in-memory `_running`
    set only de-dups scheduled ticks within one process — it does NOT cover the manual
    Quick-Run route (a double-click, or a quick run overlapping a cron tick), which
    would otherwise launch two concurrent runs that duplicate Exa/LLM spend and the
    opportunity inbox. A Postgres session-level advisory lock works across the
    per-statement pooled connections; yields True if this caller won it, False if
    another run for the brand already holds it.

    The lock is held on its connection in autocommit, so we hold only the advisory lock
    (not an idle-in-transaction) while the long, LLM/scrape-bound run proceeds on other
    pooled connections. A test double (MagicMock) yields a truthy `got`, so hermetic
    tests calling run_scout proceed normally."""
    with db.connection() as conn:
        # CRITICAL: restore autocommit before the connection returns to the pool. The
        # psycopg pool resets open transactions, NOT session attributes — a leaked
        # autocommit=True would poison the pooled connection process-wide and silently
        # defeat the single-transaction atomicity that other `with db.connection()`
        # blocks rely on (clusters.delete_cluster, publishing.unpublish/publish,
        # generation.delete_article).
        prev_autocommit = conn.autocommit
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select pg_try_advisory_lock(hashtext('rankforge:scout'), "
                    "hashtext(%s)) as got",
                    (str(business_id),),
                )
                got = bool((cur.fetchone() or {}).get("got"))
                try:
                    yield got
                finally:
                    if got:
                        cur.execute(
                            "select pg_advisory_unlock(hashtext('rankforge:scout'), "
                            "hashtext(%s))",
                            (str(business_id),),
                        )
        finally:
            conn.autocommit = prev_autocommit


async def run_scout(
    client: PowabaseClient,
    db: Database,
    *,
    business_id: UUID,
    trigger: str = "schedule",
) -> dict[str, Any]:
    """One-shot run: auto-generate a Search Plan AND execute it (the scheduled path,
    and the 'quick run' button). The two-phase manual path is plan_scout + execute_run."""
    cfg = ensure_config(db, business_id)
    brand = brands.get_profile(db, business_id)
    with _brand_run_lock(db, business_id) as got_lock:
        if not got_lock:
            # Another run for this brand is already in flight — don't duplicate spend
            # or insert a second 'running' row. Surface the in-flight run to the caller.
            log.info("scout run for %s skipped — a run is already in flight", business_id)
            return _latest_run(db, business_id) or {}
        # Drop any stale two-phase 'planned' row the user never executed (same cleanup
        # start_plan does) so a save-plan-then-Quick-Run doesn't orphan it. Planned rows
        # carry no opportunities, so this is a clean delete.
        db.execute(
            "delete from public.scout_runs where business_id = %s and status = 'planned'",
            (business_id,),
        )
        run = db.fetch_one(
            "insert into public.scout_runs (business_id, trigger, progress) "
            "values (%s, %s, %s) "
            f"returning {_RUN_COLUMNS}",
            (
                business_id, trigger,
                Json({"phase": "starting", "message": "Starting scout…"}),
            ),
        )
        run_id = run["id"]
        try:
            if brand is None:
                raise RuntimeError("brand not found")
            focus = cfg.get("focus") or brand.get("seed_topics") or []
            _set_progress(
                db, run_id, "planning", "Researching what's trending in your niche…"
            )
            cov = _gather_coverage(db, business_id)
            plan = await _generate_plan(client, brand, focus, cov)
            db.execute(
                "update public.scout_runs set plan = %s where id = %s",
                (Json(plan), run_id),
            )
            # Hand the just-built coverage snapshot to discovery so it isn't re-scanned.
            await _discover_and_store(
                client, db, run_id, business_id, brand, cfg, plan, cov
            )
        except Exception:  # noqa: BLE001 — record on the run row
            log.exception("scout run %s failed for business %s", run_id, business_id)
            db.execute(
                "update public.scout_runs set status = 'failed', error = %s "
                "where id = %s",
                ("scout run failed — see server logs", run_id),
            )
            _set_progress(db, run_id, "failed", "Scout run failed — see server logs.")
        finally:
            # Only a SCHEDULED run advances the cron; a manual "Quick run" leaves the
            # brand's automatic cadence untouched.
            if trigger == "schedule":
                _roll_schedule(db, business_id, cfg.get("cadence"))
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
    # Atomically claim the opportunity. The manual route pre-claims to 'queued'; the
    # scheduled auto-draft loop passes a 'new' opp straight in — either way THIS
    # compare-and-set is the single gate, so a user click racing the auto-draft loop
    # can't launch two full draft pipelines (duplicate Exa/LLM spend + an orphaned
    # second article). A None result means another drafter already has it.
    claimed = db.fetch_one(
        "update public.opportunities set status = 'drafting', updated_at = now() "
        "where id = %s and status in ('new', 'queued') returning id",
        (opp_id,),
    )
    if claimed is None:
        return False
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
        # run_generation_task swallows its own errors onto the article row, so a failed
        # or empty draft would otherwise fall through to 'in_review'/'drafted' and the
        # inbox would report success on an unretryable article with no body. Re-read and
        # gate: on a failed status or empty content_md, reset the opp to 'new' (so the
        # user can retry from the inbox) and leave the half-built article behind.
        written = generation.get_article(db, article["id"])
        if (
            not written
            or written.get("generation_status") == "failed"
            or not (written.get("content_md") or "").strip()
        ):
            log.warning(
                "auto_draft: generation produced no usable draft for opp %s "
                "(article %s) — returning it to the inbox",
                opp_id, article["id"],
            )
            set_opportunity_status(db, opp_id, "new")
            return False
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


def _latest_run(db: Database, business_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_RUN_COLUMNS} from public.scout_runs where business_id = %s "
        "order by created_at desc limit 1",
        (business_id,),
    )
