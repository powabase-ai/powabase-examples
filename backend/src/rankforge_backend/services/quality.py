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
# Highest-stakes correctness gate (catching hallucinations). Top model + extended
# thinking; short output makes the reasoning cheap relative to its value.
FACTCHECK_MODEL = "claude-opus-4-7"

_SYSTEM = """\
You are RankForge's **grounding fact-checker**. You analyze an article's factual \
claims against evidence drawn from its sources, and you always return structured \
JSON exactly as the instruction specifies.

## Principles
- Evidence is only the provided source text — your own prior knowledge is not evidence.
- You judge faithfulness to the sources, not real-world truth.

## Output discipline
- Return exactly one JSON object — no prose, no commentary, no code fences.
"""

# 1) Extract the checkable claims so each can be verified against evidence
#    retrieved FOR IT (avoids false positives from a generic excerpt bundle).
_EXTRACT_PROMPT = """\
List the article's specific, checkable claims — factual or statistical assertions \
(numbers, dates, named facts, comparisons). Exclude opinion, generic background, \
and transitions.

## Output
Return ONLY this JSON object (at most 14 claims, each one concise sentence):
{"claims": [str]}\
"""

# 2) Judge each claim against the evidence retrieved specifically for that claim.
_JUDGE_PROMPT = """\
You are given CLAIM / EVIDENCE pairs from an article. Judge each claim against its \
OWN evidence only.

## For each claim
- Supported — the evidence substantiates it.
- Flagged — the evidence does not support it, or there is no evidence for it.

## Scoring
- `grounding_score` (0–100): overall share of claims that are well-supported.
- `claims_checked`: number of claims judged.
- `supported`: number supported.
- `flagged`: at most 10 items, each `{claim, issue, suggestion}`.

## Output
Return ONLY this JSON object:
{"grounding_score": int, "claims_checked": int, "supported": int, "flagged": \
[{"claim": str, "issue": str, "suggestion": str}]}\
"""

# Fallback when claim extraction fails or there is no KB: judge against a broad
# excerpt bundle (the original behavior).
_BROAD_PROMPT = """\
Check the article against the SOURCE EXCERPTS provided below.

## Flag a claim when
- It is specific or statistical but the excerpts do not support it, or
- It states a fact without attributing it to any source.

## Do not flag
- General background, opinion, or transitions that make no checkable factual claim.

## Output
Return ONLY this JSON object:
{"grounding_score": int, "claims_checked": int, "supported": int, "flagged": \
[{"claim": str, "issue": str, "suggestion": str}]}\
"""

_UNAVAILABLE = {
    "grounding_score": None,
    "claims_checked": 0,
    "supported": 0,
    "flagged": [],
    "error": "fact-check unavailable",
}


async def ensure_factcheck_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=FACTCHECK_AGENT_NAME,
        model=FACTCHECK_MODEL,
        system_prompt=_SYSTEM,
        settings={"reasoning_effort": "high"},
    )


def _kb_context(
    db: Database, article: dict
) -> tuple[str | None, list[str] | None]:
    """Resolve the brand KB id and this article's source_ids (scoped retrieval)."""
    rrid = article.get("research_run_id")
    if not rrid or not article.get("business_id"):
        return None, None
    brand = brands.get_profile(db, article["business_id"])
    kb_id = brand.get("brand_kb_id") if brand else None
    if not kb_id:
        return None, None
    srcs = research_svc.list_sources(db, rrid)
    source_ids = [s["source_id"] for s in srcs if s.get("source_id")] or None
    return kb_id, source_ids


async def _broad_excerpts(
    client: PowabaseClient,
    kb_id: str,
    source_ids: list[str] | None,
    brief: dict,
) -> list[str]:
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


async def _per_claim_report(
    client: PowabaseClient,
    agent_id: str,
    md: str,
    kb_id: str,
    source_ids: list[str] | None,
) -> dict[str, Any] | None:
    """Extract claims, retrieve evidence per claim, judge the pairs. None on failure."""
    ex = await client.run_agent(agent_id, f"{_EXTRACT_PROMPT}\n\nARTICLE:\n{md[:14000]}")
    data = extract_json(ex.get("content") or "")
    claims = [
        c.strip()
        for c in (data.get("claims") or [])
        if isinstance(c, str) and c.strip()
    ][:14]
    if not claims:
        return None
    blocks: list[str] = []
    for claim in claims:
        # Recall over precision here: keep weak matches so a supported claim isn't
        # flagged just because its evidence ranked below the generation threshold.
        ev = await grounding.search(
            client, kb_id, claim, top_k=6, source_ids=source_ids, filter_weak=False
        )
        evidence = "\n".join(f"- {(c.get('text') or '')[:400]}" for c in ev) or (
            "(no matching evidence found in the sources)"
        )
        blocks.append(f"CLAIM: {claim}\nEVIDENCE:\n{evidence}")
    res = await client.run_agent(agent_id, f"{_JUDGE_PROMPT}\n\n" + "\n\n".join(blocks))
    return extract_json(res.get("content") or "")


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
    kb_id, source_ids = _kb_context(db, article)

    try:
        agent_id = await ensure_factcheck_agent(client)
    except Exception:  # noqa: BLE001 — advisory: degrade gracefully
        gen_svc._update(db, article_id, grounding_report=_UNAVAILABLE)
        return _UNAVAILABLE

    report: dict[str, Any] | None = None
    # Preferred path: verify each claim against evidence retrieved for that claim.
    if kb_id:
        try:
            report = await _per_claim_report(client, agent_id, md, kb_id, source_ids)
        except Exception:  # noqa: BLE001 — fall back to the broad-bundle judge
            report = None
    # Fallback: broad excerpt bundle (no KB, or extraction produced nothing).
    if report is None:
        try:
            excerpts = (
                await _broad_excerpts(client, kb_id, source_ids, brief) if kb_id else []
            )
            excerpt_text = "\n---\n".join(e[:600] for e in excerpts) or (
                "(no grounding excerpts available)"
            )
            res = await client.run_agent(
                agent_id,
                f"{_BROAD_PROMPT}\n\nSOURCE EXCERPTS:\n{excerpt_text}\n\nARTICLE:\n{md[:14000]}",
            )
            report = extract_json(res.get("content") or "")
        except Exception:  # noqa: BLE001
            report = _UNAVAILABLE

    gen_svc._update(db, article_id, grounding_report=report)
    return report
