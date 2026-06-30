"""Stage B — brief. Turns a research_run into an editable content brief via a
no-tools LLM agent.
"""

from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..models.brief import BriefResult, BriefUpdate
from ..powabase import PowabaseClient
from ..util import extract_json
from . import research as research_svc
from . import templates as templates_svc
from .agents import ensure_agent

BRIEF_AGENT_NAME = "rankforge-brief"
# Planning step — sets the contract every downstream agent obeys. Top model +
# extended thinking; thinking forces temperature≈1, so we omit temperature.
BRIEF_MODEL = "claude-opus-4-7"

_SYSTEM_PROMPT = """\
You are RankForge's **SEO/GEO content strategist**. You turn topic research into a \
precise, writer-ready content brief. The brief is the contract every downstream \
agent obeys: the writer drafts to your outline, the scorers grade against your \
keywords/entities/questions, and the fact-checker verifies claims. An excellent \
brief is specific enough that two different writers would produce broadly the same \
article — vague briefs produce generic, off-target drafts.

## Inputs you receive
- The topic and its search intent (informational / commercial / transactional / \
navigational) — let the intent drive the angle and the call-to-action.
- SERP results — the pages currently ranking; mine them for the table-stakes \
sub-topics and for the gap you can win.
- People-Also-Ask questions — explicit searcher demand the article must satisfy.
- Keyword clusters — the secondary keywords and the sub-topics they map to.
- Competitor-page teardowns (URLs, word counts, heading outlines) — your benchmark \
for length and coverage; aim to match the depth and beat the structure.

## Produce a brief that specifies
- **Keywords** — exactly one primary keyword (the head term the article ranks for) \
plus a focused set of supporting secondary keywords drawn from the clusters; do not \
pad with near-duplicates of the primary.
- **Target length** — a word count competitive with the ranking pages (use the \
competitor word counts as the reference, not a round guess).
- **Outline** — an ordered H2/H3 heading plan that covers the topic and improves on \
the competitors' structure. Prefix every heading with `H2:` or `H3:`; every H3 \
belongs under the preceding H2; order the sections the way a reader should travel \
the topic.
- **Entities** — the specific people, products, organizations, standards, and \
concepts the article must name to demonstrate topical authority.
- **Questions** — the searcher questions the article must answer outright (seed \
from PAA, then add the obvious follow-ups the SERP implies).
- **Links** — suggested internal and external link targets (external ones should be \
authoritative sources worth citing).
- **Metadata** — an SEO title (front-load the primary keyword, ~50–60 chars) and a \
meta description (compelling, ~120–160 chars, includes the primary keyword).

## Rules
- You may be given an **Editorial direction** (a working title + angle + primary \
keyword). When present, IT is the article's topic and point of view — your brief \
executes it. The SERP research grounds the angle in facts and optimizes it for the \
keyword; it NEVER overrides the angle. Do not let a generic, higher-ranking topic \
replace the specific framing: the `suggested_title` must deliver the working title's \
promise (sharpen it, keep its subject and stance), and the outline must argue the \
angle — not restate what already ranks.
- Ground every choice in the supplied research; introduce no facts it does not \
support. If the research is thin on a sub-topic, leave it out rather than inventing \
coverage.
- Make the outline MECE — each heading owns a distinct sub-topic, and together the \
headings cover the brief's questions and entities with no gaps or overlap. No two \
headings should answer the same question; no listed question or entity should be \
homeless.
- Match the depth to the intent: informational topics earn a fuller outline; \
transactional/navigational ones stay tight and decision-focused.
- Every secondary keyword and entity you list must have a home in the outline — \
don't list a keyword the headings never touch.
- When an article type and outline guidance are provided, shape the heading plan to \
fit it, and respect its target length over the competitor benchmark.

## Output
- Emit exactly one JSON object in a single ```json fenced block, with nothing after it.
"""

_SCHEMA_HINT = """{
  "primary_keyword": "...", "secondary_keywords": ["..."], "target_word_count": 2200,
  "headings": ["H2: ...", "H3: ..."], "entities": ["..."], "questions": ["...?"],
  "link_suggestions": {"internal": ["..."], "external": ["https://..."]},
  "suggested_title": "...", "suggested_meta": "..."
}"""

_BRIEF_COLUMNS = (
    "id, business_id, research_run_id, article_type, topic, primary_keyword, "
    "secondary_keywords, target_word_count, headings, entities, questions, "
    "link_suggestions, suggested_title, suggested_meta, created_at, updated_at"
)
_JSONB_FIELDS = {
    "secondary_keywords",
    "headings",
    "entities",
    "questions",
    "link_suggestions",
}

async def ensure_brief_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=BRIEF_AGENT_NAME,
        model=BRIEF_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"reasoning_effort": "high"},
    )


def _summarize_research(run: dict[str, Any]) -> str:
    serp = run.get("serp") or {}
    results = serp.get("results", [])[:10]
    paa = serp.get("paa", [])
    competitors = run.get("competitors", [])
    clusters = run.get("clusters", [])

    lines = [f"Topic: {run.get('topic')}", f"Search intent: {run.get('intent')}", ""]
    lines.append("Top SERP results:")
    for r in results:
        lines.append(f"  - {r.get('title')} ({r.get('url')})")
    lines.append("")
    lines.append("People Also Ask:")
    lines += [f"  - {q}" for q in paa[:15]]
    lines.append("")
    lines.append("Competitor pages (for word-count + coverage benchmarking):")
    for c in competitors:
        heads = "; ".join(c.get("headings", [])[:12])
        lines.append(
            f"  - {c.get('url')} — ~{c.get('word_count')} words; headings: {heads}"
        )
    lines.append("")
    lines.append("Keyword clusters:")
    for cl in clusters:
        lines.append(
            f"  - {cl.get('label')} ({cl.get('intent')}): "
            f"{', '.join(cl.get('keywords', [])[:8])}"
        )
    return "\n".join(lines)


def _direction_block(direction: dict[str, Any] | None) -> str:
    """The opportunity's working title + angle + keyword — the editorial contract the
    brief must execute (so a scout's specific angle isn't lost to a generic SERP)."""
    if not direction:
        return ""
    title = (direction.get("title") or "").strip()
    angle = (direction.get("angle") or "").strip()
    keyword = (direction.get("keyword") or "").strip()
    if not (title or angle):
        return ""
    lines = ["## Editorial direction — THIS is the article; execute it, don't drift"]
    if title:
        lines.append(f"- Working title: {title}")
    if angle:
        lines.append(f"- Angle / stance: {angle}")
    if keyword:
        lines.append(f"- Primary keyword to rank for: {keyword}")
    lines.append(
        "The title and angle ARE the topic and point of view. Ground them in the "
        "research below and optimize for the primary keyword, but keep the subject "
        "and stance — your suggested_title must fulfill the working title's promise, "
        "and the outline must argue this angle, not the generic topic that ranks."
    )
    return "\n".join(lines) + "\n\n"


async def generate_brief(
    client: PowabaseClient,
    db: Database,
    *,
    research_run_id: UUID,
    article_type: str | None = None,
    editorial_direction: dict[str, Any] | None = None,
    created_by: UUID | None = None,
) -> dict[str, Any]:
    run = research_svc.get_run(db, research_run_id)
    if run is None:
        raise ValueError("research run not found")

    template = templates_svc.get_template(db, article_type)
    type_block = ""
    if template:
        type_block = (
            f"\nArticle type: {template['label']}.\n"
            f"Outline guidance: {template['outline_guidance']}\n"
            f"Target length: ~{template['default_word_count']} words.\n"
            "Shape the heading outline to fit this article type.\n"
        )

    agent_id = await ensure_brief_agent(client)
    message = (
        "Create a content brief.\n"
        f"{_direction_block(editorial_direction)}"
        f"{type_block}\n"
        f"{_summarize_research(run)}\n\n"
        "Output ONLY a single ```json block matching exactly this shape:\n"
        f"{_SCHEMA_HINT}"
    )
    result = await client.run_agent(agent_id, message)
    content = result.get("content", "")
    if not content:
        raise RuntimeError(f"brief run returned no content: {result}")
    parsed = BriefResult.model_validate(extract_json(content))

    return db.fetch_one(
        f"""
        insert into public.briefs
            (business_id, research_run_id, article_type, topic, primary_keyword,
             secondary_keywords, target_word_count, headings, entities, questions,
             link_suggestions, suggested_title, suggested_meta, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning {_BRIEF_COLUMNS}
        """,
        (
            run.get("business_id"),
            research_run_id,
            article_type,
            run.get("topic"),
            parsed.primary_keyword,
            Json(parsed.secondary_keywords),
            parsed.target_word_count,
            Json(parsed.headings),
            Json(parsed.entities),
            Json(parsed.questions),
            Json(parsed.link_suggestions),
            parsed.suggested_title,
            parsed.suggested_meta,
            created_by,
        ),
    )


def get_brief(db: Database, brief_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_BRIEF_COLUMNS} from public.briefs where id = %s", (brief_id,)
    )


def list_briefs(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_BRIEF_COLUMNS} from public.briefs "
        "where business_id = %s order by created_at desc",
        (business_id,),
    )


def update_brief(
    db: Database, brief_id: UUID, data: BriefUpdate
) -> dict[str, Any] | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return get_brief(db, brief_id)
    set_clauses: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        set_clauses.append(f"{key} = %s")
        params.append(Json(value) if key in _JSONB_FIELDS else value)
    set_clauses.append("updated_at = now()")
    params.append(brief_id)
    return db.fetch_one(
        f"update public.briefs set {', '.join(set_clauses)} "
        f"where id = %s returning {_BRIEF_COLUMNS}",
        tuple(params),
    )
