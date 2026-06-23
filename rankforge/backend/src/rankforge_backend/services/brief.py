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
precise, writer-ready content brief.

## Inputs you receive
- The topic and its search intent.
- SERP results, People-Also-Ask questions, and keyword clusters.
- Competitor-page teardowns (URLs, word counts, heading outlines) for benchmarking.

## Produce a brief that specifies
- **Keywords** — one primary keyword plus the supporting secondary keywords.
- **Target length** — a word count competitive with the ranking pages.
- **Outline** — an ordered H2/H3 heading plan that covers the topic and improves on \
the competitors' structure.
- **Entities** — the people, products, and concepts the article must mention.
- **Questions** — the searcher questions the article must answer.
- **Links** — suggested internal and external link targets.
- **Metadata** — an SEO title and a meta description.

## Rules
- Ground every choice in the supplied research; introduce no facts it does not support.
- Make the outline MECE — each heading owns a distinct sub-topic, and together the \
headings cover the brief's questions and entities with no gaps or overlap.
- When an article type and outline guidance are provided, shape the heading plan to fit.

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


async def generate_brief(
    client: PowabaseClient,
    db: Database,
    *,
    research_run_id: UUID,
    article_type: str | None = None,
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
        "Create a content brief from this research.\n"
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
             link_suggestions, suggested_title, suggested_meta)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
