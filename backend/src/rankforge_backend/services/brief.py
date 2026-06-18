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

BRIEF_AGENT_NAME = "rankforge-brief"
BRIEF_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = (
    "You are RankForge's SEO/GEO content strategist. Given research about a topic "
    "(SERP, competitor teardowns, People-Also-Ask, keyword clusters) you produce a "
    "content brief for a writer: primary + secondary keywords, a target word count "
    "competitive with the SERP, a recommended H2/H3 heading outline, must-cover "
    "entities, questions to answer, internal/external link suggestions, and a "
    "suggested SEO title + meta description. Output one JSON object in a single "
    "```json fenced block and nothing after it."
)

_SCHEMA_HINT = """{
  "primary_keyword": "...", "secondary_keywords": ["..."], "target_word_count": 2200,
  "headings": ["H2: ...", "H3: ..."], "entities": ["..."], "questions": ["...?"],
  "link_suggestions": {"internal": ["..."], "external": ["https://..."]},
  "suggested_title": "...", "suggested_meta": "..."
}"""

_BRIEF_COLUMNS = (
    "id, business_id, research_run_id, topic, primary_keyword, secondary_keywords, "
    "target_word_count, headings, entities, questions, link_suggestions, "
    "suggested_title, suggested_meta, created_at, updated_at"
)
_JSONB_FIELDS = {
    "secondary_keywords",
    "headings",
    "entities",
    "questions",
    "link_suggestions",
}

_agent_id: str | None = None


async def ensure_brief_agent(client: PowabaseClient) -> str:
    global _agent_id
    if _agent_id:
        return _agent_id
    listing = await client.get_agents()
    for agent in listing.get("agents", []):
        if agent.get("name") == BRIEF_AGENT_NAME:
            _agent_id = agent["id"]
            return _agent_id
    created = await client.create_agent(
        name=BRIEF_AGENT_NAME,
        model=BRIEF_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"temperature": 0.3},
    )
    _agent_id = created.get("id") or created.get("agent", {}).get("id")
    return _agent_id


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
    client: PowabaseClient, db: Database, *, research_run_id: UUID
) -> dict[str, Any]:
    run = research_svc.get_run(db, research_run_id)
    if run is None:
        raise ValueError("research run not found")

    agent_id = await ensure_brief_agent(client)
    message = (
        "Create a content brief from this research.\n\n"
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
            (business_id, research_run_id, topic, primary_keyword, secondary_keywords,
             target_word_count, headings, entities, questions, link_suggestions,
             suggested_title, suggested_meta)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning {_BRIEF_COLUMNS}
        """,
        (
            run.get("business_id"),
            research_run_id,
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
