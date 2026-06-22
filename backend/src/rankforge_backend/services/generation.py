"""Stage C — generation. Turns a brief into a grounded long-form draft.

Backend-orchestrated (async, status-tracked): ground (brand KB from research
sources) -> outline (from the brief) -> per-section grounded drafting (KB retrieval
injected as context, cited inline) -> assemble -> store as a draft article.
"""

import re
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..powabase import PowabaseClient
from . import brief as brief_svc
from . import grounding
from . import research as research_svc

WRITER_AGENT_NAME = "rankforge-writer"
WRITER_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = (
    "You are RankForge's senior content writer. You write one part of a long-form "
    "SEO/GEO blog article at a time, in clean Markdown. Ground every factual claim in "
    "the provided source excerpts and cite the relevant source inline as a Markdown "
    "link. Write in the brand's voice — specific, useful, concrete. Never invent "
    "statistics. Output only the Markdown for the requested part (starting at its "
    "heading), with no preamble or sign-off."
)

_ARTICLE_COLUMNS = (
    "id, business_id, brief_id, research_run_id, title, slug, status, "
    "generation_status, generation_error, progress, content_md, meta_title, "
    "meta_description, seo_score, geo_score, json_ld, created_at, updated_at"
)
_SUMMARY_COLUMNS = "id, title, status, generation_status, progress, updated_at"

_writer_agent_id: str | None = None


async def ensure_writer_agent(client: PowabaseClient) -> str:
    global _writer_agent_id
    if _writer_agent_id:
        return _writer_agent_id
    listing = await client.get_agents()
    for agent in listing.get("agents", []):
        if agent.get("name") == WRITER_AGENT_NAME:
            _writer_agent_id = agent["id"]
            return _writer_agent_id
    created = await client.create_agent(
        name=WRITER_AGENT_NAME,
        model=WRITER_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"temperature": 0.4},
    )
    _writer_agent_id = created.get("id") or created.get("agent", {}).get("id")
    return _writer_agent_id


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
    chunks: list[dict[str, Any]], url_by_source: dict[str, str]
) -> str:
    if not chunks:
        return "(no grounding sources — write carefully and avoid specific claims)"
    lines = []
    for c in chunks:
        src = url_by_source.get(c.get("source_id")) or c.get("source_id") or "source"
        lines.append(f"- ({src}) {c.get('text', '')[:500]}")
    return "\n".join(lines)


def create_article(db: Database, brief: dict[str, Any]) -> dict[str, Any]:
    title = brief.get("suggested_title") or brief.get("topic") or "Untitled"
    return db.fetch_one(
        f"""
        insert into public.articles
            (business_id, brief_id, research_run_id, title, slug, status,
             generation_status, meta_title, meta_description, keywords)
        values (%s, %s, %s, %s, %s, 'draft', 'grounding', %s, %s, %s)
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
        ),
    )


def _update(db: Database, article_id: UUID, **fields: Any) -> None:
    jsonb = {"progress", "seo_score", "geo_score", "json_ld"}
    sets, params = [], []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        params.append(Json(v) if k in jsonb else v)
    sets.append("updated_at = now()")
    params.append(article_id)
    db.execute(
        f"update public.articles set {', '.join(sets)} where id = %s", tuple(params)
    )


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
) -> str:
    # Scope retrieval to THIS article's research sources within the shared brand KB.
    chunks = (
        await grounding.search(client, kb_id, search_query, source_ids=source_ids)
        if kb_id
        else []
    )
    msg = (
        f"Article topic: {ctx['topic']}\n"
        f"Primary keyword: {ctx.get('primary_keyword') or 'n/a'}\n"
        f"Secondary keywords: {', '.join(ctx.get('secondary_keywords') or [])}\n"
        f"Audience / brand: {ctx.get('audience') or 'n/a'}\n\n"
        f"{instruction}\n\n"
        f"Grounding excerpts (cite the relevant source URL inline as a Markdown link):\n"
        f"{_grounding_block(chunks, url_by_source)}\n\n"
        "Output only the Markdown."
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
        )
        parts.append(conclusion)

        content_md = "\n\n".join(p for p in parts if p)
        _update(
            db, article_id,
            content_md=content_md,
            generation_status="optimizing",
            progress={"phase": "scoring", "total": total, "done": total},
        )

        # 6) GEO optimize (JSON-LD) then SEO + GEO scoring
        #    (local import avoids a circular dependency)
        from . import geo_optimize, scoring

        await geo_optimize.optimize_and_store(client, db, article_id)
        await scoring.score_and_store(client, db, article_id)

        _update(
            db, article_id,
            generation_status="done",
            progress={"phase": "done", "total": total, "done": total,
                      "word_count": len(content_md.split())},
        )
    except Exception as e:  # noqa: BLE001
        _update(db, article_id, generation_status="failed", generation_error=str(e))


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
