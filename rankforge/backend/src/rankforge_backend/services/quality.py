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
You are RankForge's **grounding fact-checker** — the pipeline's hallucination gate. \
You analyze an article's factual claims against evidence drawn from its own scraped \
sources, and you always return structured JSON exactly as the instruction specifies. \
Your verdicts drive an automated revision loop: flagged claims become rewrite \
instructions, so a missed hallucination ships and a false flag wastes a revision. \
Precision matters in both directions.

## Principles
- Evidence is only the provided source text — your own prior knowledge, however \
confident, is not evidence and must never be used to confirm a claim.
- You judge faithfulness to the sources, not real-world truth: a claim that happens \
to be true in the world but is unsupported by the evidence is still unsupported.
- Judge each claim only against the evidence paired with it; do not let one claim's \
evidence vouch for another.
- Be strict on specifics (numbers, dates, named facts, comparisons) — these are \
exactly what answer engines quote and what readers trust. Do not flag general \
background, opinion, or framing that makes no checkable factual assertion.
- When evidence is absent or only loosely related, treat the claim as unsupported \
rather than giving it the benefit of the doubt.

## Output discipline
- Return exactly one JSON object — no prose, no commentary, no code fences.
"""

# 1) Extract the checkable claims so each can be verified against evidence
#    retrieved FOR IT (avoids false positives from a generic excerpt bundle).
_EXTRACT_PROMPT = """\
List the article's specific, checkable claims — factual or statistical assertions \
that could be verified against a source (numbers, percentages, dates, named facts, \
attributions, head-to-head comparisons, superlatives like "the largest/first").

## Include
- Quantified or dated statements, named entities doing specific things, and \
explicit comparisons or rankings.

## Exclude
- Opinion, advice, predictions, generic background, definitions, and transitions \
that assert no verifiable fact.

## For each claim return two things
- `claim` — a self-contained restatement: resolve pronouns and carry enough context \
that it can be checked on its own, without the surrounding article.
- `quote` — a SHORT excerpt (a phrase or sentence, <= 25 words) copied **verbatim** \
from the article — the exact words that make the assertion, so it can be located and \
edited. Copy it character-for-character; do not paraphrase the quote.

## Output
Return ONLY this JSON object (at most 14 claims):
{"claims": [{"claim": str, "quote": str}]}\
"""

# 2) Judge each claim against the evidence retrieved specifically for that claim.
_JUDGE_PROMPT = """\
You are given CLAIM / QUOTE / EVIDENCE triples from an article. Judge each claim \
strictly against its OWN evidence block only — never against another claim's evidence \
or your own knowledge. The QUOTE is the article's own verbatim wording.

## For each claim
- Supported — the evidence directly substantiates the specific assertion, including \
its numbers and named facts.
- Flagged — the evidence contradicts it, supports only a weaker/different version, \
or there is no relevant evidence at all (an empty or off-topic evidence block means \
flag, not pass).

## When you flag
- `quote` — copy the claim's QUOTE through VERBATIM (the article's exact words), so \
the writer and reader can find the sentence to edit.
- `issue` — say precisely what the evidence does not back (e.g. "evidence gives 12%, \
claim says 30%"; "no source mentions this figure").
- `suggestion` — a concrete fix: correct the figure to match the source, attribute \
the claim to the source that supports it, soften it to what the evidence allows, or \
cut it.

## Scoring
- `grounding_score` (0–100): overall share of claims that are well-supported (treat \
it as supported/checked × 100).
- `claims_checked`: number of claims judged.
- `supported`: number supported.
- `flagged`: at most 10 items, each `{claim, quote, issue, suggestion}`.

## Output
Return ONLY this JSON object:
{"grounding_score": int, "claims_checked": int, "supported": int, "flagged": \
[{"claim": str, "quote": str, "issue": str, "suggestion": str}]}\
"""

# Fallback when claim extraction fails or there is no KB: judge against a broad
# excerpt bundle (the original behavior).
_BROAD_PROMPT = """\
Check the article against the SOURCE EXCERPTS provided below. The excerpts are the \
only evidence — treat anything outside them, including your own knowledge, as \
unsupported. Work through the article's specific factual and statistical claims and \
judge each against the excerpts as a whole.

## Flag a claim when
- It is specific or statistical (a number, date, named fact, or comparison) but the \
excerpts do not support it — or contradict it, or support only a weaker version, or
- It states a checkable fact without attributing it to any source.

## Do not flag
- General background, opinion, advice, definitions, or transitions that make no \
checkable factual claim.

## For each flag give
- `quote` — the article's exact verbatim wording that makes the claim (a phrase or \
sentence, copied character-for-character), so the sentence can be located and edited.
- `issue` — what the excerpts fail to back; `suggestion` — a concrete fix (correct \
to the source, attribute it, soften it, or cut it).

## Scoring
- `grounding_score` (0–100): the share of checkable claims that the excerpts \
support; `claims_checked`/`supported`: the count you judged and the count supported.

## Output
Return ONLY this JSON object:
{"grounding_score": int, "claims_checked": int, "supported": int, "flagged": \
[{"claim": str, "quote": str, "issue": str, "suggestion": str}]}\
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
    claims: list[dict[str, str]] = []
    for c in (data.get("claims") or [])[:14]:
        if isinstance(c, dict) and (c.get("claim") or "").strip():
            claims.append(
                {"claim": c["claim"].strip(), "quote": (c.get("quote") or "").strip()}
            )
        elif isinstance(c, str) and c.strip():  # tolerate the old bare-string shape
            claims.append({"claim": c.strip(), "quote": ""})
    if not claims:
        return None
    blocks: list[str] = []
    for item in claims:
        # Retrieve evidence for the self-contained claim. Recall over precision: keep
        # weak matches so a supported claim isn't flagged just because its evidence
        # ranked below the generation threshold.
        ev = await grounding.search(
            client, kb_id, item["claim"], top_k=6, source_ids=source_ids,
            filter_weak=False,
        )
        evidence = "\n".join(f"- {(c.get('text') or '')[:400]}" for c in ev) or (
            "(no matching evidence found in the sources)"
        )
        quote_line = f'QUOTE: "{item["quote"]}"\n' if item["quote"] else ""
        blocks.append(f"CLAIM: {item['claim']}\n{quote_line}EVIDENCE:\n{evidence}")
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

    # No KB → grounding isn't measurable. Report it as unavailable (score None)
    # rather than asking the judge to "check" against no evidence, which would
    # otherwise feed a meaningless number into satisfied()/combined_score.
    if not kb_id:
        gen_svc._update(db, article_id, grounding_report=_UNAVAILABLE)
        return _UNAVAILABLE

    try:
        agent_id = await ensure_factcheck_agent(client)
    except Exception:  # noqa: BLE001 — advisory: degrade gracefully
        gen_svc._update(db, article_id, grounding_report=_UNAVAILABLE)
        return _UNAVAILABLE

    report: dict[str, Any] | None = None
    # Preferred path: verify each claim against evidence retrieved for that claim.
    try:
        report = await _per_claim_report(client, agent_id, md, kb_id, source_ids)
    except Exception:  # noqa: BLE001 — fall back to the broad-bundle judge
        report = None
    # Fallback: broad excerpt bundle (extraction failed or produced nothing).
    if report is None:
        try:
            excerpts = await _broad_excerpts(client, kb_id, source_ids, brief)
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
