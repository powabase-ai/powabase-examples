"""Reflection / fact-check (quality gate). Checks the draft's specific claims against
the grounding sources and produces a grounding report. Advisory — never blocks
publish; if the check is unavailable it degrades gracefully.
"""

from typing import Any
from uuid import UUID

from ..db import Database
from ..powabase import PowabaseClient
from ..util import extract_json
from . import brief as brief_svc
from . import business_profiles as brands
from . import generation as gen_svc
from . import grounding
from . import research as research_svc
from .agents import ensure_agent

FACTCHECK_AGENT_NAME = "rankforge-factcheck"
FACTCHECK_MODEL = "claude-sonnet-4-6"

_SYSTEM = """\
You are RankForge's **grounding fact-checker**. You judge whether an article's \
claims are supported by a given set of source excerpts, and you return only \
structured JSON.

## What "supported" means
- A claim is supported only if the SOURCE EXCERPTS substantiate it; your own prior \
knowledge is not evidence.
- You judge faithfulness to the sources, not real-world truth.

## Output discipline
- Return exactly one JSON object — no prose, no commentary, no code fences.
"""
_PROMPT = """\
Check the article against the SOURCE EXCERPTS provided below.

## Steps
- Identify the article's specific factual and statistical claims (numbers, dates, \
named facts, comparative assertions).
- For each claim, decide whether the excerpts substantiate it.

## Flag a claim when
- It is specific or statistical but the excerpts do not support it (possible \
hallucination), or
- It states a fact without attributing it to any source.

## Do not flag
- General background, opinion, or transitions that make no checkable factual claim.

## Scoring
- `grounding_score` (0–100): how well-grounded the article is overall.
- `claims_checked`: how many claims you assessed.
- `supported`: how many of those were substantiated.
- `flagged`: at most 10 items, each `{claim, issue, suggestion}`.

## Output
Return ONLY this JSON object:
{"grounding_score": int, "claims_checked": int, "supported": int, "flagged": \
[{"claim": str, "issue": str, "suggestion": str}]}\
"""

async def ensure_factcheck_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=FACTCHECK_AGENT_NAME,
        model=FACTCHECK_MODEL,
        system_prompt=_SYSTEM,
        settings={"temperature": 0},
    )


async def _grounding_excerpts(
    client: PowabaseClient, db: Database, article: dict, brief: dict
) -> list[str]:
    rrid = article.get("research_run_id")
    if not rrid or not article.get("business_id"):
        return []
    brand = brands.get_profile(db, article["business_id"])
    kb_id = brand.get("brand_kb_id") if brand else None
    if not kb_id:
        return []
    srcs = research_svc.list_sources(db, rrid)
    source_ids = [s["source_id"] for s in srcs if s.get("source_id")] or None
    queries = [
        brief.get("primary_keyword"),
        brief.get("topic"),
        *(brief.get("secondary_keywords") or [])[:3],
    ]
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if not q:
            continue
        for c in await grounding.search(client, kb_id, q, top_k=8, source_ids=source_ids):
            cid = c.get("chunk_id")
            if cid and cid in seen:
                continue
            if cid:
                seen.add(cid)
            out.append(c.get("text", ""))
            if len(out) >= 20:
                return out
    return out


async def reflect(
    client: PowabaseClient, db: Database, article_id: UUID
) -> dict[str, Any] | None:
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    brief = (
        brief_svc.get_brief(db, article["brief_id"]) if article.get("brief_id") else {}
    ) or {}
    md = article.get("content_md") or ""

    excerpts = await _grounding_excerpts(client, db, article, brief)
    excerpt_text = "\n---\n".join(e[:600] for e in excerpts) or (
        "(no grounding excerpts available)"
    )
    try:
        agent_id = await ensure_factcheck_agent(client)
        res = await client.run_agent(
            agent_id,
            f"{_PROMPT}\n\nSOURCE EXCERPTS:\n{excerpt_text}\n\nARTICLE:\n{md[:14000]}",
        )
        report = extract_json(res.get("content") or "")
    except Exception:  # noqa: BLE001 — advisory: degrade gracefully
        report = {
            "grounding_score": None,
            "claims_checked": 0,
            "supported": 0,
            "flagged": [],
            "error": "fact-check unavailable",
        }
    gen_svc._update(db, article_id, grounding_report=report)
    return report
