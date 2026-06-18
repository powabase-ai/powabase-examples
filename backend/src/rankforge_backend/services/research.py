"""Stage A — research. Provisions (once) a research agent with web_search +
web_scrape, runs it for a topic scoped to a brand, parses its structured output,
and stores a research_run.
"""

import json
import re
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..models.research import ResearchResult
from ..powabase import PowabaseClient
from . import business_profiles as brands

RESEARCH_AGENT_NAME = "rankforge-research"
RESEARCH_MODEL = "claude-sonnet-4-6"

# depth → (serp results to analyze, competitor pages to scrape)
DEPTH_PRESETS = {"quick": (5, 3), "standard": (10, 5), "deep": (20, 10)}

_SYSTEM_PROMPT = (
    "You are RankForge's SEO/GEO research analyst. Given a topic you research the "
    "search landscape using web_search (Exa) and web_scrape (Firecrawl): analyze the "
    "SERP, tear down the top competitor pages, extract People-Also-Ask questions and "
    "related queries, cluster keywords, and classify search intent. Always finish your "
    "turn by outputting a single JSON object with the research inside one ```json "
    "fenced code block, and write nothing after that block."
)

_SCHEMA_HINT = """{
  "topic": "...", "locale": "en-US", "intent": "informational|commercial|transactional|navigational",
  "serp": [{"rank": 1, "title": "...", "url": "...", "snippet": "..."}],
  "paa": ["question?"],
  "related_queries": ["..."],
  "competitors": [{"url": "...", "title": "...", "word_count": 1800,
    "headings": ["H2 ...", "H3 ..."], "entities": ["..."], "has_schema": true, "published_at": "2025-..."}],
  "keyword_clusters": [{"label": "...", "keywords": ["..."], "intent": "informational"}]
}"""

_RESEARCH_COLUMNS = (
    "id, business_id, topic, locale, serp, competitors, clusters, intent, "
    "agent_run_id, created_by, created_at"
)

# cache the provisioned agent id for the process
_agent_id: str | None = None


async def ensure_research_agent(client: PowabaseClient) -> str:
    """Get-or-create the shared research agent (idempotent by name)."""
    global _agent_id
    if _agent_id:
        return _agent_id

    listing = await client.get_agents()
    for agent in listing.get("agents", []):
        if agent.get("name") == RESEARCH_AGENT_NAME:
            _agent_id = agent["id"]
            return _agent_id

    created = await client.create_agent(
        name=RESEARCH_AGENT_NAME,
        model=RESEARCH_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        settings={"temperature": 0},
    )
    agent_id = created.get("id") or created.get("agent", {}).get("id")
    for tool in ("web_search", "web_scrape"):
        await client.attach_builtin_tool(agent_id, tool)
    _agent_id = agent_id
    return agent_id


def _build_message(brand: dict[str, Any], topic: str, locale: str, depth: str) -> str:
    serp_n, scrape_n = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["deep"])
    competitors = ", ".join(c.get("domain", "") for c in brand.get("competitors", []))
    return (
        f"Research this topic for a blog article.\n\n"
        f"Topic: {topic}\n"
        f"Locale: {locale}\n"
        f"Brand niche: {brand.get('niche') or 'n/a'}\n"
        f"Brand audience: {brand.get('audience') or 'n/a'}\n"
        f"Known competitor domains: {competitors or 'none provided'}\n\n"
        f"Depth: {depth} — analyze the top {serp_n} organic results and scrape the "
        f"top {scrape_n} competitor pages.\n\n"
        "Steps:\n"
        f"1. web_search the topic; collect the top {serp_n} organic results "
        "(rank, title, url, snippet), People-Also-Ask questions, and related queries.\n"
        f"2. web_scrape the top {scrape_n} competitor pages; for each extract title, "
        "approximate word_count, the H2/H3 heading outline, key entities/subtopics, "
        "and whether it has schema markup.\n"
        "3. Cluster the keywords and classify the overall search intent.\n\n"
        "Output ONLY a single ```json block matching exactly this shape:\n"
        f"{_SCHEMA_HINT}"
    )


def _extract_json(content: str) -> dict[str, Any]:
    """Pull the JSON object from the agent's final message (fenced or bare)."""
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in agent output")
        raw = content[start : end + 1]
    return json.loads(raw)


async def run_research(
    client: PowabaseClient,
    db: Database,
    *,
    business_id: UUID,
    topic: str,
    locale: str = "en-US",
    depth: str = "deep",
) -> dict[str, Any]:
    brand = brands.get_profile(db, business_id)
    if brand is None:
        raise ValueError("business profile not found")

    agent_id = await ensure_research_agent(client)
    message = _build_message(brand, topic, locale, depth)
    run = await client.run_agent_collect(agent_id, message)
    if run["error"]:
        raise RuntimeError(f"research run failed: {run['error']}")

    parsed = ResearchResult.model_validate(_extract_json(run["content"]))
    serp = {
        "results": [r.model_dump() for r in parsed.serp],
        "paa": parsed.paa,
        "related_queries": parsed.related_queries,
    }
    return db.fetch_one(
        f"""
        insert into public.research_runs
            (business_id, topic, locale, serp, competitors, clusters, intent, agent_run_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning {_RESEARCH_COLUMNS}
        """,
        (
            business_id,
            topic,
            locale,
            Json(serp),
            Json([c.model_dump() for c in parsed.competitors]),
            Json([c.model_dump() for c in parsed.keyword_clusters]),
            parsed.intent,
            run["run_id"],
        ),
    )


def get_run(db: Database, run_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_RESEARCH_COLUMNS} from public.research_runs where id = %s",
        (run_id,),
    )


def list_runs(db: Database, business_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_RESEARCH_COLUMNS} from public.research_runs "
        "where business_id = %s order by created_at desc",
        (business_id,),
    )
