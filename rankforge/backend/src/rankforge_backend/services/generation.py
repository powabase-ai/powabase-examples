"""Stage C — generation. Turns a brief into a grounded long-form draft.

Backend-orchestrated (async, status-tracked): ground (brand KB from research
sources) -> broad grounding retrieval -> whole-article drafting in one streamed pass
(retrieval injected as context, cited inline) -> store as a draft article.
"""

import asyncio
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
# Long-form prose IS the product — top model. Temperature (not extended thinking) for
# natural variety: one big streamed draft, where thinking would add the most latency.
WRITER_MODEL = "claude-opus-4-7"

_SYSTEM_PROMPT = """\
You are RankForge's **senior content writer**. You write a complete long-form \
SEO/GEO blog article in one pass, in clean Markdown, for the brand's audience. \
Excellent work reads like a knowledgeable human wrote it for that audience: it \
answers the reader's question fast, builds a single argument from start to finish, \
backs specifics with real sources, and never betrays its machine origin.

## What you're given, and how to use it
- The article topic, primary and secondary keywords, and the brand/audience — write \
to that reader, work the primary keyword in naturally where it fits (never stuff \
it), and weave the relevant secondary keywords in only where they read smoothly.
- The outline (an ordered H2/H3 heading plan) and a target length — write the whole \
article to it: a short intro, every section in order with its subheadings, and a \
conclusion.
- Grounding excerpts with their source domains — your evidence for every specific \
claim (see below).

## Write it as ONE coherent article, not stitched-together sections
- Find the throughline — the single argument the article makes — and have every \
section advance it. Set it up early; pay it off at the end.
- Write real transitions: each section picks up where the last left off and sets up \
the next, so the reader is carried from start to finish.
- Say each thing once. Don't re-introduce the topic mid-article, re-explain a concept \
two sections apart, or let the conclusion restate the intro — it should resolve it.
- Vary section length and rhythm on purpose; uniform, same-shaped sections read \
machine-made.

## Grounding & citations
- Base every factual or statistical claim on the provided source excerpts; never \
invent statistics or specifics.
- Cite sources inline as Markdown links.
- **Only ever link to a URL that appears VERBATIM in the provided source excerpts or \
brand-materials block. Copy it exactly — character for character.** Never invent, \
guess, shorten, or extend a URL, and never append a path you think is plausible (e.g. \
`/docs/...`, `/blog/...`, `/SECURITY.md`). If you have no exact URL for a claim, state \
the claim without a link rather than fabricating one. A dead link is worse than none.
- Make each link's anchor text a natural, descriptive phrase that reads well in the \
sentence — never the source's page title, the site name, or a bare URL.
- Spread citations across DIFFERENT source domains. Across the whole article, don't \
lean on any single source for more than about two citations. If only a couple of \
sources are available, cite sparingly rather than linking the same page again and again.

## Style
- Write in the brand's voice: specific, useful, concrete.
- Lead with a tight, directly extractable answer, then elaborate.

## Position the brand (editorial, never an ad)
- You may be given a "Your brand's own materials" block: excerpts from the brand's own pages, each with its URL. Treat these as the brand's authoritative voice — its real product, capabilities, terminology, and first-party data.
- Use them for what only the brand can provide: its specific approach, feature names, concrete examples, and its own data/results. Prefer the brand's own example or number over a generic one when the materials supply it.
- When the topic is something the brand actually does or solves, present the brand as one concrete, credible option — name the specific capability and link to the exact page that goes deeper (an internal link with natural anchor text). Earn the mention by being useful, not by selling.
- **This is the brand's own blog — when the article weighs the brand against competitors (named in the brief), write from the brand's side.** Be accurate and fair, but lead with the brand's genuine strengths and don't undersell it or hand a competitor an edge the evidence doesn't justify. A reader on the brand's blog should come away understanding where the brand is the stronger choice. Ground every comparative claim in the sources or brand materials — never invent superiority, and where the brand is honestly weaker, say less rather than something false. Real, supported strengths advocate for the brand; puffery the fact-checker will flag does not.
- Keep it proportional: a mention or two woven into the argument where it genuinely helps — not a section-ending plug, not in every section.
- Use the brand's own terminology accurately (don't rename its products or features), and never claim a capability the materials don't support.
- Never write marketing slogans, CTAs, or "sign up today" copy.

## Content cluster
- You may be given a "Your place in the … content cluster" block. If you're a SUPPORTING article, weave in ONE natural link up to the named pillar and stay tightly on your subtopic (don't re-cover the pillar's breadth). If you're the PILLAR, be comprehensive — you're the hub the others link to. Follow that block's specifics.

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
- Output the full article body in Markdown: the intro, every section (`##` with its \
`###` subheadings), then a conclusion, in outline order.
- Do NOT output the H1 title — it's added for you. Add no preamble, sign-off, or \
meta-commentary.
"""

_ARTICLE_COLUMNS = (
    "id, business_id, brief_id, research_run_id, title, slug, status, "
    "generation_status, generation_error, progress, content_md, meta_title, "
    "meta_description, seo_score, geo_score, readability_score, json_ld, "
    "grounding_report, canonical_url, cluster_id, cluster_role, created_at, updated_at"
)
_SUMMARY_COLUMNS = "id, title, status, generation_status, progress, updated_at"

async def ensure_writer_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=WRITER_AGENT_NAME,
        model=WRITER_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        # max_tokens is the OUTPUT ceiling (input/grounding is separate, on the 200k
        # context window). One pass emits the whole article — a generous ceiling so a
        # long pillar piece is never truncated; the model still writes to the brief's
        # target_word_count and stops, so this costs nothing extra in practice.
        settings={"temperature": 0.4, "max_tokens": 32000},
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
    per_source: int = 3,
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


def try_begin_generation(db: Database, article_id: UUID) -> bool:
    """Atomically (re)claim an article for generation — used to retry a draft that
    failed or was interrupted by a restart. Flips to 'grounding' and clears the
    prior error, but only if no pipeline is already running, so a double-submit
    can't launch two concurrent generations on the same article."""
    placeholders = ", ".join(["%s"] * len(_ACTIVE_GEN_STATUSES))
    row = db.fetch_one(
        f"update public.articles set generation_status = 'grounding', "
        f"generation_error = null, progress = %s, updated_at = now() "
        f"where id = %s and (generation_status is null "
        f"or generation_status not in ({placeholders})) returning id",
        (
            Json({"phase": "grounding"}),
            article_id,
            *_ACTIVE_GEN_STATUSES,
        ),
    )
    return row is not None


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


async def _gather_grounding(
    client: PowabaseClient,
    kb_id: str | None,
    queries: list[str | None],
    *,
    source_ids: list[str] | None = None,
    top_k: int = 8,
    per_source: int = 2,
    per_query: int = 3,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Retrieve grounding across the WHOLE article's scope by running several queries
    (primary keyword, topic, secondary keywords, section headings) CONCURRENTLY, then
    giving each query a fair share (`per_query`) of the result — so a broad early
    query can't fill the budget and starve the heading queries (which is what leaves
    later sections with no evidence). Deduped by chunk; capped per source for variety."""
    qs = [q for q in queries if q]
    if not kb_id or not qs:
        return []
    results = await asyncio.gather(
        *(
            grounding.search(client, kb_id, q, top_k=top_k, source_ids=source_ids)
            for q in qs
        )
    )
    seen: set[str] = set()
    per: dict[Any, int] = {}
    out: list[dict[str, Any]] = []
    for res in results:  # one list per query — each contributes up to `per_query`
        got = 0
        for c in res:
            if len(out) >= limit:
                return out
            if got >= per_query:
                break
            cid, sid = c.get("chunk_id"), c.get("source_id")
            if cid and cid in seen:
                continue
            if sid and per.get(sid, 0) >= per_source:  # only cap real source ids
                continue
            if cid:
                seen.add(cid)
            if sid:
                per[sid] = per.get(sid, 0) + 1
            out.append(c)
            got += 1
    return out


def _outline_text(headings: list[str]) -> str:
    lines: list[str] = []
    for h in headings:
        text = h.split(":", 1)[1].strip() if ":" in h else h.strip()
        if h.lower().lstrip().startswith("h3"):
            lines.append(f"  - {text}  (### subsection)")
        else:
            lines.append(f"- {text}  (## section)")
    return "\n".join(lines) or "- (no outline provided — structure the article yourself)"


def _brand_context_block(brand: dict[str, Any] | None, audience: str | None) -> str:
    """Tell the writer whose blog this is and who the competitors are, so it writes
    FROM the brand's side — advocating for it (within reason, only where the facts
    justify) instead of giving a competitor the edge on the brand's own blog."""
    name = (brand or {}).get("name")
    if not name:
        return f"- Audience / brand: {audience or 'n/a'}"
    lines = [f"- This is **{name}**'s own blog — write as {name}, for {name}'s audience."]
    if audience:
        lines.append(f"- Audience: {audience}")
    if (brand or {}).get("description"):
        lines.append(f"- What {name} is: {brand['description']}")
    comps = [
        (c.get("name") or c.get("domain"))
        for c in ((brand or {}).get("competitors") or [])
        if isinstance(c, dict) and (c.get("name") or c.get("domain"))
    ]
    if comps:
        lines.append(
            f"- Competitors (do NOT promote these): {', '.join(comps[:10])}. When the "
            f"article compares them with {name}, stay accurate but lead with {name}'s "
            f"genuine, source-backed strengths — never undersell {name} or hand a "
            "competitor an edge the evidence doesn't justify."
        )
    return "\n".join(lines)


def _cluster_block(cluster: dict[str, Any] | None) -> str:
    """Tell the writer its place in the content cluster: a supporting MEMBER (stay on
    its subtopic, link up to the pillar) or the authoritative PILLAR (be comprehensive)."""
    if not cluster:
        return ""
    label = cluster.get("cluster") or "this"
    if cluster.get("role") == "member" and cluster.get("pillar_url"):
        return (
            f"\n\n## Your place in the \"{label}\" content cluster\n"
            "- This is a SUPPORTING article in the cluster. Its pillar (the broad, "
            f'authoritative hub) is "{cluster.get("pillar_title")}" at '
            f"{cluster['pillar_url']}.\n"
            "- Link UP to the pillar once, with natural descriptive anchor text, where "
            "it genuinely fits.\n"
            "- Stay tightly focused on THIS article's specific subtopic — do not "
            "re-cover the pillar's full breadth, which would compete with it."
        )
    if cluster.get("role") == "pillar":
        members = [m for m in (cluster.get("members") or []) if m]
        line = (
            "- This is the PILLAR — the authoritative hub of its cluster. Be "
            "comprehensive and cover the topic broadly; supporting articles link up "
            "to you."
        )
        if members:
            line += (
                " Related cluster articles you can reference where relevant: "
                f"{', '.join(members[:12])}."
            )
        return f'\n\n## Your place in the "{label}" content cluster\n{line}'
    return ""


def _cluster_context(
    db: Database, article_id: UUID, brand: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Resolve this article's cluster framing for the writer: its pillar (if a member)
    or its members (if the pillar). None if unclustered or undeterminable."""
    from . import clusters as clusters_svc
    from . import linking

    art = get_article(db, article_id)
    cid = (art or {}).get("cluster_id")
    if not (art and cid):
        return None
    cl = clusters_svc.get_cluster(db, cid)
    if not cl:
        return None
    role = art.get("cluster_role")
    if role == "member":
        pid = cl.get("pillar_article_id")
        if pid and pid != article_id and (pillar := get_article(db, pid)):
            purl = linking.canonical_url(brand, pillar)
            if purl:
                return {
                    "role": "member", "cluster": cl.get("label"),
                    "pillar_title": pillar.get("title"), "pillar_url": purl,
                }
        return None
    if role == "pillar":
        members = db.fetch_all(
            "select title from public.articles where cluster_id = %s "
            "and cluster_role = 'member' and status = 'published'",
            (cid,),
        )
        return {
            "role": "pillar", "cluster": cl.get("label"),
            "members": [m["title"] for m in members],
        }
    return None


async def _draft_article(
    client: PowabaseClient,
    agent_id: str,
    brief: dict[str, Any],
    ctx: dict[str, Any],
    *,
    title: str,
    kb_id: str | None,
    source_ids: list[str] | None,
    url_by_source: dict[str, str],
    materials_kb_id: str | None = None,
    materials_url_by_source: dict[str, str] | None = None,
    brand: dict[str, Any] | None = None,
    cluster: dict[str, Any] | None = None,
) -> str:
    """Draft the WHOLE article in one streamed pass, so the model holds the entire
    piece in context and writes a single coherent argument (the per-section approach
    produced disjoint, stitched-together drafts)."""
    headings = brief.get("headings") or []
    h2s = [
        h.split(":", 1)[1].strip()
        for h in headings
        if h.lower().lstrip().startswith("h2") and ":" in h
    ]
    # Bound the query set so the fan-out stays sane: primary + topic + a few
    # secondaries + every H2 (capped), so each section topic gets its own retrieval.
    queries: list[str | None] = [
        ctx.get("primary_keyword"),
        ctx["topic"],
        *(ctx.get("secondary_keywords") or [])[:5],
        *h2s[:12],
    ]
    # Scope research to this article's own sources; pull brand materials brand-wide.
    research = await _gather_grounding(
        client, kb_id, queries, source_ids=source_ids
    )
    # NB: keep this distinct from the `brand` PROFILE param — it's the brand's
    # materials-KB grounding excerpts (a list), used only to build brand_block.
    brand_grounding = await _gather_grounding(
        client, materials_kb_id, queries, per_query=2, limit=30
    )

    cluster_block = _cluster_block(cluster)
    brand_block = ""
    if brand_grounding:
        brand_block = (
            "\n\n## Your brand's own materials (describe accurately, link as internal links)\n"
            "- Where the article genuinely calls for it, work in the brand's real "
            "capabilities and link to the relevant page with natural anchor text. "
            "Editorial, not an ad — only where it adds value.\n"
            f"{_grounding_block(brand_grounding, materials_url_by_source or {})}"
        )
    wc = brief.get("target_word_count") or 1800
    msg = (
        "## The article to write\n"
        f"- Title (already the H1): {title}\n"
        f"- Topic: {ctx['topic']}\n"
        f"- Primary keyword (use naturally): {ctx.get('primary_keyword') or 'n/a'}\n"
        f"- Secondary keywords: {', '.join(ctx.get('secondary_keywords') or []) or 'n/a'}\n"
        f"{_brand_context_block(brand, ctx.get('audience'))}\n"
        f"- Target length: ~{wc} words\n\n"
        "## Outline — write every section, in this order\n"
        f"{_outline_text(headings)}\n\n"
        "## Grounding excerpts\n"
        "- Cite an excerpt inline with natural anchor text (a descriptive phrase, "
        "never the page title or a bare URL), and vary the source domain.\n"
        f"{_grounding_block(research, url_by_source)}"
        f"{cluster_block}"
        f"{brand_block}\n\n"
        "## Output\n"
        "- Output the full article body in Markdown (intro, every section, "
        "conclusion). Do not include the H1 title."
    )
    # Stream: the whole article is too large for the buffered /run endpoint.
    res = await client.run_agent_collect(agent_id, msg)
    if res.get("error"):
        raise RuntimeError(f"draft failed: {res['error']}")
    body = (res.get("content") or "").strip()
    # A real article is thousands of chars. An empty/near-empty body (a refusal or a
    # broken stream) would otherwise ship as a bare "# Title" stub — fail loudly so
    # the task is marked failed and the user can retry, rather than producing junk.
    if len(body) < 500:
        raise RuntimeError(f"draft too short ({len(body)} chars) — empty or refused")
    return body


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
        # 0) cluster placement — assign this article to a content cluster unless it
        #    already inherited one from an opportunity (manual articles land here).
        if business_id:
            from . import clusters  # local import avoids a service import cycle

            art = get_article(db, article_id)
            if art and not art.get("cluster_id"):
                try:
                    cid, role = await clusters.assign(
                        client, db, business_id,
                        title=art.get("title") or topic,
                        keyword=brief.get("primary_keyword"),
                        angle=topic,
                    )
                    clusters.attach_article(db, article_id, cid, role)
                except Exception:  # noqa: BLE001 — clustering shouldn't block drafting
                    log.exception("cluster assignment failed for article %s", article_id)

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
            # Only ground on usable sources (extracted + enough real content) so the
            # writer cites real articles, not failed/thin scrapes.
            run_sources = [
                s
                for s in research_svc.list_sources(db, research_run_id)
                if research_svc.is_usable_source(s)
            ]
            source_ids = [
                s["source_id"] for s in run_sources if s.get("source_id")
            ] or None
            url_by_source = {
                s["source_id"]: s["url"]
                for s in run_sources
                if s.get("source_id") and s.get("url")
            }

        # 1b) the brand's OWN materials KB — for on-brand narrative + internal links.
        #     The profile also carries the brand name/positioning/competitors so the
        #     writer knows whose blog this is and writes from the brand's side.
        materials_kb_id: str | None = None
        materials_url_by_source: dict[str, str] = {}
        brand_profile: dict[str, Any] | None = None
        if business_id:
            brand_profile = brands.get_profile(db, business_id)
            brand = brand_profile
            materials_kb_id = brand.get("materials_kb_id") if brand else None
            if materials_kb_id:
                materials_url_by_source = {
                    s["source_id"]: s["url"]
                    for s in brand_materials.list_sources(db, business_id)
                    if s.get("source_id")
                    and s.get("status") == "extracted"
                    and s.get("url")
                }

        # 2) draft the WHOLE article in one coherent pass (the per-section approach
        #    produced stitched-together, disjoint drafts)
        agent_id = await ensure_writer_agent(client)
        title = brief.get("suggested_title") or topic
        cluster_ctx = _cluster_context(db, article_id, brand_profile)
        _update(
            db, article_id,
            generation_status="drafting",
            progress={"phase": "drafting", "total": 1, "done": 0},
        )
        body = await _draft_article(
            client, agent_id, brief, ctx, title=title,
            kb_id=kb_id, source_ids=source_ids, url_by_source=url_by_source,
            materials_kb_id=materials_kb_id,
            materials_url_by_source=materials_url_by_source,
            brand=brand_profile,
            cluster=cluster_ctx,
        )
        # Prepend the canonical H1. Strip ANY H1 line the writer emitted anyway
        # (multiline — a stray H1 after a preamble line would otherwise leave the
        # article with two titles). H2/H3 (`## `, `### `) are untouched.
        body = re.sub(r"(?m)^[ \t]*#[ \t]+[^\n]*\n?", "", body).strip()
        content_md = f"# {title}\n\n{body}".strip()
        _update(
            db, article_id,
            content_md=content_md,
            generation_status="optimizing",
            progress={"phase": "scoring", "total": 1, "done": 1},
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
        # Best-effort, BEFORE flipping to done so the Links panel is accurate the moment
        # the article is ready: validate outbound links so a fabricated/dead URL (the
        # writer can emit a plausible-but-dead deep link even when grounded) is flagged
        # immediately, not at publish time. Never let a link-check failure fail the run.
        if business_id:
            # Local import: linkcheck imports generation at module level, so a top-level
            # import here would form a real generation<->linkcheck cycle.
            from . import linkcheck

            try:
                await linkcheck.check_article(db, business_id, article_id)
            except Exception:  # noqa: BLE001
                log.exception("post-generation link check failed for %s", article_id)
        _update(
            db, article_id,
            generation_status="done",
            progress={"phase": "done", "total": 1, "done": 1,
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


def delete_article(db: Database, article_id: UUID) -> bool:
    """Hard-delete an article. Its versions, publications, comments, link suggestions,
    and broken-link findings cascade; cluster membership is FK ON DELETE SET NULL (a
    deleted pillar leaves its cluster pillar-less, not removed). Articles own no
    per-article Powabase resource, so there's nothing to clean up remotely. Returns
    whether a row was deleted.

    LIMITATION: APPLIED internal links baked into OTHER articles' bodies as
    `rf:article/{deleted_id}` are NOT rewritten here — after delete they resolve to a
    dead /p/{deleted_id} preview. We log the citing articles (below) so an operator can
    re-link/re-score them, but we deliberately do not edit other articles' content_md."""
    # Find articles that cite this one (via staged suggestions or recorded link health)
    # BEFORE the delete cascades those rows away, so the log names the affected pieces.
    citing = db.fetch_all(
        "select distinct article_id from public.link_suggestions "
        "where target_article_id = %s and article_id <> %s",
        (article_id, article_id),
    )
    citing_ids = [r["article_id"] for r in citing]
    if citing_ids:
        log.warning(
            "deleting article %s leaves applied internal links dangling in %d "
            "article(s): %s — re-link/re-score them",
            article_id, len(citing_ids), citing_ids,
        )
    # An opportunity drafted into this article points at it. The FK would null its
    # article_id but leave it stranded in a dead 'drafted' state (no inbox action) —
    # so return it to the inbox first, ready to re-draft or dismiss. One transaction:
    # the opportunity reset and the delete commit together or not at all.
    with db.connection() as conn:
        conn.execute(
            "update public.opportunities set status = 'new', article_id = null, "
            "updated_at = now() where article_id = %s and status = 'drafted'",
            (article_id,),
        )
        deleted = conn.execute(
            "delete from public.articles where id = %s returning id", (article_id,)
        ).fetchone()
    return deleted is not None


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
