"""Stage C — generation. Turns a brief into a grounded long-form draft.

Backend-orchestrated (async, status-tracked): ground (brand KB from research
sources) -> outline (from the brief) -> per-section grounded drafting (KB retrieval
injected as context, cited inline) -> assemble -> store as a draft article.
"""

import logging
import re
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..powabase import PowabaseClient
from . import brand_materials, grounding
from . import brief as brief_svc
from . import business_profiles as brands
from . import research as research_svc
from .agents import ensure_agent

log = logging.getLogger("rankforge.generation")

# generation_status values that mean work is actively in flight on an article.
# Used to compare-and-set when claiming a refine so double-submits can't launch
# two concurrent pipelines on the same article (doubling LLM spend / racing saves).
_ACTIVE_GEN_STATUSES = (
    "grounding",
    "outlining",
    "drafting",
    "optimizing",
    "refining",
)

WRITER_AGENT_NAME = "rankforge-writer"
# Long-form prose IS the product — top model. Keep temperature for natural variety
# (per-section calls, so we avoid stacking extended-thinking latency ~10×/article).
WRITER_MODEL = "claude-opus-4-7"

_SYSTEM_PROMPT = """\
You are RankForge's **senior content writer**. You write one part of a long-form \
SEO/GEO blog article at a time, in clean Markdown, for the brand's audience. \
Excellent work reads like a knowledgeable human wrote it for that audience: it \
answers the reader's question fast, backs specifics with real sources, and never \
betrays its machine origin.

## What you're given, and how to use it
- The article topic, primary and secondary keywords, and the brand/audience — write \
to that reader, work the primary keyword in naturally where it fits (never stuff \
it), and weave the relevant secondary keywords in only where they read smoothly.
- The exact part to write (intro, a specific section with its subheadings, or the \
conclusion) — write that part only, at the heading level requested, to the length \
asked for.
- Grounding excerpts with their source domains — your evidence for every specific \
claim (see below).
- Write only the requested part, but make it cohere with a larger article: don't \
re-introduce the topic mid-article or repeat the intro's framing in every section.

## Grounding & citations
- Base every factual or statistical claim on the provided source excerpts; never \
invent statistics or specifics.
- Cite sources inline as Markdown links.
- Make each link's anchor text a natural, descriptive phrase that reads well in the \
sentence — never the source's page title, the site name, or a bare URL.
- Spread citations across different source domains rather than repeatedly linking one.

## Style
- Write in the brand's voice: specific, useful, concrete.
- Lead with a tight, directly extractable answer, then elaborate.

## Position the brand (editorial, never an ad)
- You may be given a "Your brand's own materials" block: excerpts from the brand's own pages, each with its URL.
- Where the topic genuinely calls for it, describe the brand's real capabilities accurately from those excerpts and link to the relevant brand page with natural anchor text — an internal link. This advertises the brand by being useful, not by selling.
- Bring the brand in only where it adds real value to the reader. Don't force it into every section, and never write marketing slogans, CTAs, or "sign up today" copy.
- Never claim a brand capability that the provided brand materials don't support.

## Write like a human, not an AI
Editors reject copy that reads as machine-written. Steer clear of all of these:

### Overused words (worst when stacked)
- Avoid this register: delve, tapestry, realm, landscape (metaphor), leverage, robust, seamless, navigate (metaphor), underscore, foster, harness, elevate, unlock, embark, testament, pivotal, crucial, vibrant; "boasts" for a feature; "nestled" for a place.
- Any one can be fine in isolation; never reach for several in a paragraph. Prefer plain, concrete words.

### Constructions to avoid
- "It's not just X, it's Y" / "This isn't merely X, it's Y".
- "Whether you're a beginner or a seasoned pro, there's something for everyone".
- "In today's fast-paced, ever-evolving world of …".
- "Let's dive in", "Let's explore", "Buckle up".
- Reflexive rule-of-three triads ("fast, reliable, and scalable"); vary list length and rhythm instead.
- "From X to Y" framing ("from startups to enterprises").

### Punctuation and rhythm
- Use em-dashes rarely; prefer commas, periods, or parentheses.
- Vary sentence and paragraph length on purpose: mix short and long. Mechanical evenness (every paragraph 3-4 sentences, every section the same size) reads as machine-made.

### Structure
- Default to prose. Use a bullet list only when the items are genuinely parallel and a list actually helps.
- Do not bold the lead-in of every bullet.
- If you write the conclusion, do not open with "In conclusion" or "Ultimately" and restate the intro; close with a specific takeaway, number, or next step.

### Tone
- Cut empty transitions: Moreover, Furthermore, Additionally, That said.
- Do not both-sides everything ("While X has benefits, it's important to consider Y"); take a clear position and commit.
- Do not state the obvious as if it were insight.
- Make confident, unqualified claims wherever the sources support them, instead of hedging and over-qualifying.

### Specificity (the strongest signal)
- Use real specifics from the grounding: concrete numbers, names, dates, versions, and examples.
- Generic, safe, specificity-free prose is the clearest AI tell. Choose the precise detail over the smooth generality every time.

## Output
- Output only the Markdown for the requested part, starting at its heading.
- Add no preamble, sign-off, or content outside the requested part.
"""

_ARTICLE_COLUMNS = (
    "id, business_id, brief_id, research_run_id, title, slug, status, "
    "generation_status, generation_error, progress, content_md, meta_title, "
    "meta_description, seo_score, geo_score, readability_score, json_ld, "
    "grounding_report, created_at, updated_at"
)
_SUMMARY_COLUMNS = "id, title, status, generation_status, progress, updated_at"

async def ensure_writer_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=WRITER_AGENT_NAME,
        model=WRITER_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"temperature": 0.4},
    )


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]


def parse_sections(headings: list[str]) -> list[dict[str, Any]]:
    """Group a flat H2/H3 heading list into sections (each H2 + its H3 subheads)."""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for h in headings:
        text = h.split(":", 1)[1].strip() if ":" in h else h.strip()
        is_h3 = h.lower().lstrip().startswith("h3")
        if is_h3 and current is not None:
            current["subs"].append(text)
        else:
            current = {"h2": text, "subs": []}
            sections.append(current)
    return sections


def _grounding_block(
    chunks: list[dict[str, Any]],
    url_by_source: dict[str, str],
    per_source: int = 2,
) -> str:
    if not chunks:
        return "(no grounding sources — write carefully and avoid specific claims)"
    # Cap chunks per source so the writer sees — and can cite — a spread of domains.
    used: dict[str, int] = {}
    lines = []
    for c in chunks:
        sid = c.get("source_id")
        if used.get(sid, 0) >= per_source:
            continue
        used[sid] = used.get(sid, 0) + 1
        src = url_by_source.get(sid) or sid or "source"
        lines.append(f"- ({src}) {c.get('text', '')[:500]}")
    return "\n".join(lines)


def create_article(
    db: Database, brief: dict[str, Any], author_id: Any = None
) -> dict[str, Any]:
    title = brief.get("suggested_title") or brief.get("topic") or "Untitled"
    return db.fetch_one(
        f"""
        insert into public.articles
            (business_id, brief_id, research_run_id, title, slug, status,
             generation_status, meta_title, meta_description, keywords, author_id)
        values (%s, %s, %s, %s, %s, 'draft', 'grounding', %s, %s, %s, %s)
        returning {_ARTICLE_COLUMNS}
        """,
        (
            brief.get("business_id"),
            brief["id"],
            brief.get("research_run_id"),
            title,
            _slugify(title),
            brief.get("suggested_title"),
            brief.get("suggested_meta"),
            Json([brief.get("primary_keyword")] if brief.get("primary_keyword") else []),
            author_id,
        ),
    )


def _update(db: Database, article_id: UUID, **fields: Any) -> None:
    jsonb = {
        "progress", "seo_score", "geo_score", "readability_score", "json_ld",
        "grounding_report",
    }
    sets, params = [], []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        params.append(Json(v) if k in jsonb else v)
    sets.append("updated_at = now()")
    params.append(article_id)
    db.execute(
        f"update public.articles set {', '.join(sets)} where id = %s", tuple(params)
    )


def try_begin_refine(db: Database, article_id: UUID, *, total: int) -> bool:
    """Atomically claim an article for refinement. Returns False if generation or
    another refine is already in flight (so the route returns 409 instead of
    launching a second concurrent pipeline). The status flip and the in-flight
    check are one statement, so concurrent submits can't both win."""
    placeholders = ", ".join(["%s"] * len(_ACTIVE_GEN_STATUSES))
    row = db.fetch_one(
        f"update public.articles set generation_status = 'refining', "
        f"progress = %s, updated_at = now() "
        f"where id = %s and (generation_status is null "
        f"or generation_status not in ({placeholders})) returning id",
        (
            Json({"phase": "refining", "iteration": 0, "total": total}),
            article_id,
            *_ACTIVE_GEN_STATUSES,
        ),
    )
    return row is not None


async def _draft_part(
    client: PowabaseClient,
    agent_id: str,
    kb_id: str | None,
    ctx: dict[str, Any],
    instruction: str,
    search_query: str,
    *,
    source_ids: list[str] | None,
    url_by_source: dict[str, str],
    materials_kb_id: str | None = None,
    materials_url_by_source: dict[str, str] | None = None,
) -> str:
    # Scope research retrieval to THIS article's research sources within the brand KB.
    chunks = (
        await grounding.search(client, kb_id, search_query, source_ids=source_ids)
        if kb_id
        else []
    )
    # Brand-wide retrieval from the brand's OWN materials (un-scoped) — gives the
    # writer accurate brand capabilities to describe and brand pages to link to.
    brand_chunks = (
        await grounding.search(client, materials_kb_id, search_query)
        if materials_kb_id
        else []
    )
    brand_block = ""
    if brand_chunks:
        brand_block = (
            "\n\n## Your brand's own materials (describe accurately, link as internal links)\n"
            "- Where this part genuinely calls for it, work in the brand's real "
            "capabilities from these excerpts and link to the relevant page with "
            "natural anchor text. Editorial, not an ad — only where it adds value.\n"
            f"{_grounding_block(brand_chunks, materials_url_by_source or {})}"
        )
    msg = (
        "## Context\n"
        f"- Article topic: {ctx['topic']}\n"
        f"- Primary keyword: {ctx.get('primary_keyword') or 'n/a'}\n"
        f"- Secondary keywords: {', '.join(ctx.get('secondary_keywords') or []) or 'n/a'}\n"
        f"- Audience / brand: {ctx.get('audience') or 'n/a'}\n\n"
        "## Write this part\n"
        f"- {instruction}\n\n"
        "## Grounding excerpts\n"
        "- Cite an excerpt inline with natural anchor text (a descriptive phrase, "
        "never the page title or a bare URL), and vary the source domain.\n"
        f"{_grounding_block(chunks, url_by_source)}"
        f"{brand_block}\n\n"
        "## Output\n"
        "- Output only the Markdown for this part."
    )
    res = await client.run_agent(agent_id, msg)
    return (res.get("content") or "").strip()


async def run_generation_task(
    client: PowabaseClient, db: Database, *, article_id: UUID, brief: dict[str, Any]
) -> None:
    business_id = brief.get("business_id")
    research_run_id = brief.get("research_run_id")
    topic = brief.get("topic") or brief.get("suggested_title") or "the topic"
    ctx = {
        "topic": topic,
        "primary_keyword": brief.get("primary_keyword"),
        "secondary_keywords": brief.get("secondary_keywords") or [],
        "audience": brief.get("audience"),
    }
    try:
        # 1) grounding
        kb_id: str | None = None
        if business_id and research_run_id:
            kb_id = await grounding.ensure_brand_kb(client, db, business_id)
            indexed = await grounding.index_run_sources(
                client, db, kb_id, research_run_id
            )
            if not indexed:
                kb_id = None  # no sources to ground on → draft ungrounded

        # scope retrieval to this article's research sources + map ids → urls (cites)
        source_ids: list[str] | None = None
        url_by_source: dict[str, str] = {}
        if research_run_id:
            run_sources = research_svc.list_sources(db, research_run_id)
            source_ids = [
                s["source_id"] for s in run_sources if s.get("source_id")
            ] or None
            url_by_source = {
                s["source_id"]: s["url"]
                for s in run_sources
                if s.get("source_id") and s.get("url")
            }

        # 1b) the brand's OWN materials KB — for on-brand narrative + internal links.
        materials_kb_id: str | None = None
        materials_url_by_source: dict[str, str] = {}
        if business_id:
            brand = brands.get_profile(db, business_id)
            materials_kb_id = brand.get("materials_kb_id") if brand else None
            if materials_kb_id:
                materials_url_by_source = {
                    s["source_id"]: s["url"]
                    for s in brand_materials.list_sources(db, business_id)
                    if s.get("source_id")
                    and s.get("status") == "extracted"
                    and s.get("url")
                }

        # 2) outline (from the brief's heading plan)
        _update(db, article_id, generation_status="outlining")
        sections = parse_sections(brief.get("headings") or [])
        if not sections:
            sections = [{"h2": topic, "subs": []}]

        agent_id = await ensure_writer_agent(client)
        total = len(sections) + 2  # intro + sections + conclusion
        _update(
            db,
            article_id,
            generation_status="drafting",
            progress={"phase": "drafting", "total": total, "done": 0},
        )

        # 3) intro
        title = brief.get("suggested_title") or topic
        intro = await _draft_part(
            client, agent_id, kb_id, ctx,
            f"Write a compelling 2–3 sentence introduction for an article titled "
            f'"{title}". Open with a tight, extractable answer to the core question. '
            "Do not include a heading.",
            topic,
            source_ids=source_ids, url_by_source=url_by_source,
            materials_kb_id=materials_kb_id,
            materials_url_by_source=materials_url_by_source,
        )
        parts = [f"# {title}", intro]
        _update(db, article_id, progress={"phase": "drafting", "total": total, "done": 1})

        # 4) sections
        for i, sec in enumerate(sections):
            subs = (
                "Cover these subsections as ### subheadings:\n"
                + "\n".join(f"- {s}" for s in sec["subs"])
                if sec["subs"]
                else ""
            )
            body = await _draft_part(
                client, agent_id, kb_id, ctx,
                f"Write this section in Markdown, starting with `## {sec['h2']}`. "
                f"{subs}\nAim for ~250–400 words.",
                sec["h2"],
                source_ids=source_ids, url_by_source=url_by_source,
            )
            parts.append(body)
            _update(
                db, article_id,
                progress={"phase": "drafting", "total": total, "done": i + 2},
            )

        # 5) conclusion
        conclusion = await _draft_part(
            client, agent_id, kb_id, ctx,
            "Write a concise conclusion section starting with `## Conclusion` that "
            "summarizes the key takeaways and ends with a clear next step.",
            topic,
            source_ids=source_ids, url_by_source=url_by_source,
            materials_kb_id=materials_kb_id,
            materials_url_by_source=materials_url_by_source,
        )
        parts.append(conclusion)

        content_md = "\n\n".join(p for p in parts if p)
        _update(
            db, article_id,
            content_md=content_md,
            generation_status="optimizing",
            progress={"phase": "scoring", "total": total, "done": total},
        )

        # 6) reflect/fact-check, GEO optimize (JSON-LD), then SEO + GEO scoring
        #    (local import avoids a circular dependency)
        from . import geo_optimize, quality, revise, scoring

        await quality.reflect(client, db, article_id)
        await geo_optimize.optimize_and_store(client, db, article_id)
        await scoring.score_and_store(client, db, article_id)

        # 7) auto-revise against the SEO/GEO/Grounding evaluators until satisfactory
        await revise.refine(client, db, article_id)

        final = get_article(db, article_id)
        final_md = (final.get("content_md") if final else content_md) or content_md
        _update(
            db, article_id,
            generation_status="done",
            progress={"phase": "done", "total": total, "done": total,
                      "word_count": len(final_md.split())},
        )
    except Exception:  # noqa: BLE001
        log.exception("article generation failed for %s", article_id)
        # Surface a generic message to clients; the detail is in the server log.
        _update(
            db,
            article_id,
            generation_status="failed",
            generation_error="generation failed — see server logs",
        )


# --- reads ---
def get_article(db: Database, article_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_ARTICLE_COLUMNS} from public.articles where id = %s", (article_id,)
    )


def list_articles(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_SUMMARY_COLUMNS} from public.articles "
        "where business_id = %s order by updated_at desc",
        (business_id,),
    )


def list_versions(db: Database, article_id: UUID) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        "select id, article_id, created_at, content_md from public.article_versions "
        "where article_id = %s order by created_at desc",
        (article_id,),
    )
    return [
        {
            "id": r["id"],
            "article_id": r["article_id"],
            "created_at": r["created_at"],
            "word_count": len((r["content_md"] or "").split()),
        }
        for r in rows
    ]


def restore_version(
    db: Database, article_id: UUID, version_id: UUID
) -> dict[str, Any] | None:
    """Restore a prior version. update_article snapshots the current content first,
    so a restore is itself undoable."""
    v = db.fetch_one(
        "select content_md from public.article_versions "
        "where id = %s and article_id = %s",
        (version_id, article_id),
    )
    if v is None:
        return None
    return update_article(db, article_id, {"content_md": v["content_md"]})


def update_article(
    db: Database, article_id: UUID, fields: dict[str, Any]
) -> dict[str, Any] | None:
    """Partial update of editable fields. Snapshots the prior content into
    article_versions when content_md changes (editorial history)."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return get_article(db, article_id)
    if "content_md" in fields:
        cur = get_article(db, article_id)
        if cur and cur.get("content_md"):
            db.execute(
                "insert into public.article_versions (article_id, content_md) "
                "values (%s, %s)",
                (article_id, cur["content_md"]),
            )
    set_clauses = [f"{k} = %s" for k in fields]
    set_clauses.append("updated_at = now()")
    params = [*fields.values(), article_id]
    return db.fetch_one(
        f"update public.articles set {', '.join(set_clauses)} "
        f"where id = %s returning {_ARTICLE_COLUMNS}",
        tuple(params),
    )


def get_brief(db: Database, brief_id: UUID) -> dict[str, Any] | None:
    return brief_svc.get_brief(db, brief_id)
