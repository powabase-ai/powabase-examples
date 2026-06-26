"""Auto-revision loop.

After the first draft is scored, iterate it against the built-in evaluators —
SEO, GEO, and Grounding — until it meets target (or stops improving). Each pass
feeds the failing signals' concrete fixes and the flagged grounding claims to a
`rankforge-reviser` agent, plus a fresh spread of diverse-domain source excerpts,
then re-runs fact-check → JSON-LD → scoring. Capped so it always terminates.
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

REVISER_AGENT_NAME = "rankforge-reviser"
# The "make it satisfactory" full-article rewrite — top model. Keep a low
# temperature (faithful edits) rather than extended thinking, since this is a
# large streamed output where thinking would add the most latency.
REVISER_MODEL = "claude-opus-4-7"
# Metadata is a trivial one-liner — a fast capable model is plenty.
META_MODEL = "claude-sonnet-4-6"

GROUNDING_TARGET = 70
MAX_REVISIONS = 2
_SIGNAL_FLOOR = 70  # on a FAILING axis, surface fixes for signals below this
# A single sub-signal this low warrants a targeted pass even when its parent axis
# already meets target overall (e.g. one SEO aspect at 20 while the weighted total is
# 88). Matches scoring.py's readability "egregious tell" gate, so "critical" means the
# same thing everywhere.
_CRITICAL_FLOOR = 40

_SYSTEM = """\
You are RankForge's **revising editor**. You take a full SEO/GEO blog article plus a \
list of concrete issues, and return an improved full article that resolves them.

## Preserve
- The article's structure, headings, voice, and factually-correct existing content \
and citations.
- Any accurate brand mentions and internal links to the brand's own pages — keep the \
brand's presence and those links; don't strip them out while editing.
- The article's stance FOR the brand — it's the brand's own blog. Keep it on the \
brand's side: where the draft weighs the brand against competitors, preserve (and, \
where the sources justify, sharpen) the brand's genuine strengths; don't neutralize \
the advocacy or let a competitor read as the better choice on grounds the evidence \
doesn't support. Never add superiority the sources don't back — fix grounding by being \
accurate, not by puffing.
- Roughly the same length or longer — never truncate the article.

## Fix
- Every issue in the provided list, using the supplied additional sources where relevant.

## Citations
- Weave each link into a natural descriptive phrase — never the page title or a bare URL.
- Spread citations across different source domains.
- Never invent statistics or sources.

## De-AI the prose (remove these tells, even if they aren't in the issue list)
A draft that reads as AI-written is not "improved". As you revise, actively rewrite out every one of these:

### Overused words (worst when stacked)
- delve, tapestry, realm, landscape (metaphor), leverage, robust, seamless, navigate (metaphor), underscore, foster, harness, elevate, unlock, embark, testament, pivotal, crucial, vibrant, "boasts", "nestled". Replace with plain words; never several in a paragraph.

### Constructions to delete
- "It's not just X, it's Y"; "Whether you're a beginner or a seasoned pro"; "In today's fast-paced, ever-evolving world"; "Let's dive in / Let's explore"; reflexive rule-of-three triads; "From X to Y".

### Rhythm and punctuation
- Thin out em-dashes (prefer commas, periods, parentheses).
- Break mechanical evenness: vary sentence and paragraph length; do not leave every paragraph at 3-4 sentences or every section the same size.

### Structure
- Convert bullet lists to prose where the items aren't truly parallel or prose reads better. Remove bolded lead-ins from bullets.
- Replace an "In conclusion"/"Ultimately" ending that just restates the intro with a concrete close (a specific takeaway, number, or next step).

### Tone
- Cut empty transitions (Moreover, Furthermore, Additionally, That said), reflexive both-sidesing, the obvious-stated-as-profound, and over-hedging. Make the confident claims the sources support.

### Specificity
- Push in real specifics from the sources: numbers, names, dates, versions, examples. Vagueness is the strongest tell; replace smooth generalities with precise detail.

## Output
- Output ONLY the full revised article in Markdown, starting at the H1 — no preamble, \
notes, or commentary.
"""

async def ensure_reviser_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=REVISER_AGENT_NAME,
        model=REVISER_MODEL,
        system_prompt=_SYSTEM,
        # whole-article rewrites — a generous OUTPUT ceiling so a long article isn't
        # truncated (input is separate, on the context window). See ensure_writer_agent.
        settings={"temperature": 0.2, "max_tokens": 32000},
    )


META_AGENT_NAME = "rankforge-meta"
_META_SYSTEM = """\
You are RankForge's **SEO metadata writer**. You write the search-snippet title and \
meta description for an article — the title and summary a searcher sees in Google \
results before they click. Excellent metadata earns the click: it leads with the \
primary keyword, reads naturally (not keyword-stuffed), promises the article's value, \
and respects the character limits so it never truncates in the SERP. You return only \
structured JSON.

## How to write each field
- Title — front-load the primary keyword, make it specific and compelling, and fit \
the length budget; avoid clickbait and ALL-CAPS.
- Description — one or two sentences that summarize the payoff and include the \
primary keyword once, naturally; write to entice a click, not to repeat the title.

## Output discipline
- Return exactly one JSON object — no prose, no code fences.
"""
_META_KEYS = {"keyword_title", "keyword_early", "title_length", "meta_length"}


async def ensure_meta_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=META_AGENT_NAME,
        model=META_MODEL,
        system_prompt=_META_SYSTEM,
        settings={"temperature": 0},
    )


def _meta_failing(seo: dict | None) -> bool:
    """True if SEO loses points on title/meta-bound signals (content can't fix these)."""
    return bool(seo) and any(
        s.get("key") in _META_KEYS and s.get("score", 100) < _SIGNAL_FLOOR
        for s in seo.get("signals", [])
    )


async def fix_meta(
    client: PowabaseClient, db: Database, article_id: UUID, article: dict, brief: dict
) -> None:
    """Rewrite meta_title / meta_description to satisfy the title/meta SEO signals."""
    pk = brief.get("primary_keyword") or ""
    msg = (
        "Write SEO metadata for the article.\n\n"
        "## Context\n"
        f"- Primary keyword: {pk or 'n/a'}\n"
        f"- Working title: {article.get('title') or ''}\n"
        "- Stay faithful to what the working title says the article is about; sharpen "
        "it, don't change the subject.\n\n"
        "## Requirements\n"
        "- `meta_title`: at most 60 characters, includes the primary keyword.\n"
        "- `meta_description`: 120–160 characters, compelling, includes the primary "
        "keyword.\n"
        "- Front-load the primary keyword, read naturally (no stuffing), and make the "
        "description earn the click.\n\n"
        "## Output\n"
        'Return ONLY this JSON object:\n'
        '{"meta_title": str, "meta_description": str}'
    )
    try:
        agent_id = await ensure_meta_agent(client)
        res = await client.run_agent(agent_id, msg)
        data = extract_json(res.get("content") or "")
    except Exception:  # noqa: BLE001 — advisory
        return
    fields: dict[str, Any] = {}
    if (mt := (data.get("meta_title") or "").strip()):
        fields["meta_title"] = mt
    if (md := (data.get("meta_description") or "").strip()):
        fields["meta_description"] = md
    if fields:
        gen_svc._update(db, article_id, **fields)


def _gap(score: dict) -> int:
    """Points short of target on one axis (0 once met)."""
    return max(0, score["target"] - score["total"])


def _decide(cur: list[dict], new: list[dict]) -> bool:
    """Accept a revision iff it closes (or holds) the combined gap-to-target across
    axes without pushing an already-met axis below its target. Measuring
    distance-to-target rather than raw totals means an above-target axis can't veto
    a revision that improves a failing one."""
    for c, n in zip(cur, new, strict=True):
        if c["total"] >= c["target"] and n["total"] < c["target"]:
            return False  # don't regress a met axis below its target
    cur_gap = sum(_gap(c) for c in cur)
    new_gap = sum(_gap(n) for n in new)
    # Allow gap-neutral edits through; the outer combined-score check (which also
    # weighs grounding) then decides whether the pass actually helped.
    return new_gap <= cur_gap


def _det_scores(
    md: str, title: str, meta: str | None, brief: dict
) -> list[dict]:
    """Cheap deterministic SEO + GEO scores (no LLM) for the commit gate.

    Readability is intentionally NOT here: human-ness is owned by the editorial
    loop's LLM editor, not a deterministic tell-count. The commit gate only protects
    the OBJECTIVE axes — so an SEO/GEO-preserving rewrite (whether for SEO fixes or
    for voice) is judged on those, and a good de-AI rewrite can't be vetoed by a
    tell-counter."""
    from . import scoring

    return [
        scoring.score_seo(md, title, meta, brief),
        scoring.score_geo(md, brief, None, has_structured_data=True),
    ]


def _accept_revision(
    cur_md: str, new_md: str, title: str, meta: str | None, brief: dict
) -> bool:
    """True if `new_md` doesn't regress the objective SEO/GEO axes vs `cur_md`."""
    return _decide(
        _det_scores(cur_md, title, meta, brief),
        _det_scores(new_md, title, meta, brief),
    )


# --- evaluation helpers (pure) ---
def _critical_signals(score: dict | None) -> list[dict]:
    """Sub-signals so low they warrant a fix even on an otherwise-met axis — excluding
    title/meta-bound ones the body reviser can't touch (fix_meta owns those)."""
    if not score:
        return []
    return [
        s
        for s in score.get("signals", [])
        if s.get("key") not in _META_KEYS and s.get("score", 100) < _CRITICAL_FLOOR
    ]


def collect_issues(
    seo: dict | None,
    geo: dict | None,
    grounding_report: dict | None,
    readability: dict | None = None,
) -> list[str]:
    """Turn weak evaluator signals into concrete revision instructions.

    A FAILING axis surfaces every signal below the working floor (70). A MET axis only
    surfaces a CRITICALLY low signal (<40): the axis is already good overall, so we
    don't flood the reviser, but a single egregious aspect (one signal at 20) still
    gets fixed rather than silently ignored."""
    issues: list[str] = []
    for score in (seo, geo, readability):
        if not score:
            continue
        floor = _CRITICAL_FLOOR if score.get("met") else _SIGNAL_FLOOR
        for s in score.get("signals", []):
            # Skip title/meta-bound signals — the body reviser can't fix those;
            # fix_meta() handles them. Sending them here just wastes a pass.
            if s.get("key") in _META_KEYS:
                continue
            sc = s.get("score", 100)
            if sc >= floor:
                continue
            fixes = s.get("fixes", [])
            if fixes:
                for fix in fixes:
                    issues.append(f"[{s['label']}] {fix}")
            elif sc < _CRITICAL_FLOOR:
                # Critically low but the scorer offered no canned fix — still name the
                # weak aspect so an important issue isn't silently skipped.
                issues.append(
                    f"[{s['label']}] Scored {sc}/100 — "
                    f"{s.get('explanation', '')} Improve this aspect.".strip()
                )
    if grounding_report:
        for f in (grounding_report.get("flagged") or [])[:6]:
            # Lead with the article's VERBATIM wording (the `quote`) so the reviser
            # can find the exact sentence to fix — not a paraphrase it has to hunt for.
            loc = (f.get("quote") or f.get("claim") or "")[:120]
            issues.append(
                f'[Grounding] In "{loc}" — {f.get("issue", "")} '
                f'— {f.get("suggestion", "")}'.strip()
            )
    return issues


def satisfied(
    seo: dict | None,
    geo: dict | None,
    grounding_report: dict | None,
    readability: dict | None = None,
) -> bool:
    if not (seo and seo.get("met")):
        return False
    if not (geo and geo.get("met")):
        return False
    # A met axis can still hide a critically weak aspect (one signal at 20 while the
    # weighted total is 88). Pursue it — fixing it lifts the axis total, so the loop's
    # post-rescore gate keeps the pass; once it clears 40, this stops blocking.
    if _critical_signals(seo) or _critical_signals(geo):
        return False
    if readability is not None and not readability.get("met"):
        return False
    gs = grounding_report.get("grounding_score") if grounding_report else None
    return gs is None or gs >= GROUNDING_TARGET


# A previously-met axis may dip this far below its target while we fix a FAILING one
# (it was met with margin); a bigger drop means the fix did real collateral damage.
_MET_TOLERANCE = 4


def _objective_total(
    seo: dict | None, geo: dict | None, grounding_report: dict | None
) -> float:
    """SEO + GEO + grounding — the axes the objective loop drives. (Grounding is only
    known AFTER the fact-check, which is why the loop decides post-rescore.)"""
    t = (seo or {}).get("total", 0) + (geo or {}).get("total", 0)
    g = (grounding_report or {}).get("grounding_score")
    return t + (g if isinstance(g, (int, float)) else 0)


def _met_regressed_badly(pairs: list[tuple[dict | None, dict | None]]) -> bool:
    """True if a previously-met axis fell more than _MET_TOLERANCE below target."""
    for old, new in pairs:
        if (
            old and old.get("met") and new
            and new.get("total", 0) < new.get("target", 0) - _MET_TOLERANCE
        ):
            return True
    return False


# --- context (diverse-domain excerpts) ---
async def _diverse_excerpts(
    client: PowabaseClient,
    kb_id: str | None,
    brief: dict,
    source_ids: list[str] | None,
    url_by_source: dict[str, str],
    *,
    limit: int = 12,
    per_source: int = 2,
) -> str:
    if not kb_id:
        return "(no additional sources available)"
    queries = [
        brief.get("primary_keyword"),
        brief.get("topic"),
        *(brief.get("secondary_keywords") or [])[:3],
    ]
    seen_chunk: set[str] = set()
    per: dict[str, int] = {}
    lines: list[str] = []
    for q in queries:
        if not q:
            continue
        for c in await grounding.search(
            client, kb_id, q, top_k=8, source_ids=source_ids
        ):
            cid, sid = c.get("chunk_id"), c.get("source_id")
            if cid and cid in seen_chunk:
                continue
            if per.get(sid, 0) >= per_source:
                continue
            if cid:
                seen_chunk.add(cid)
            per[sid] = per.get(sid, 0) + 1
            url = url_by_source.get(sid) or sid or "source"
            lines.append(f"- ({url}) {c.get('text', '')[:400]}")
            if len(lines) >= limit:
                return "\n".join(lines)
    return "\n".join(lines) or "(no additional sources available)"


def _article_context(
    db: Database, article: dict
) -> tuple[list[str] | None, dict[str, str], str | None]:
    """Derive (source_ids, url_by_source, kb_id) from the article's research run."""
    source_ids: list[str] | None = None
    url_by_source: dict[str, str] = {}
    rrid = article.get("research_run_id")
    if rrid:
        srcs = research_svc.list_sources(db, rrid)
        source_ids = [s["source_id"] for s in srcs if s.get("source_id")] or None
        url_by_source = {
            s["source_id"]: s["url"]
            for s in srcs
            if s.get("source_id") and s.get("url")
        }
    kb_id = None
    if article.get("business_id"):
        brand = brands.get_profile(db, article["business_id"])
        kb_id = brand.get("brand_kb_id") if brand else None
    return source_ids, url_by_source, kb_id


async def _revise_once(
    client: PowabaseClient, agent_id: str, md: str, issues: list[str], excerpts: str
) -> str:
    from . import linking  # local: avoid import cycle

    # Mask internal-link refs so the rewrite can't mangle/drop them; restore after.
    md, refmap = linking.mask_refs(md)
    issue_text = "\n".join(f"- {i}" for i in issues[:14])
    msg = (
        "Revise the article below into an improved full article.\n\n"
        "## Issues to fix\n"
        f"{issue_text}\n\n"
        "## Additional sources you may cite\n"
        "- Use natural anchor text and vary the source domain.\n"
        f"{excerpts}\n\n"
        "## Output\n"
        "- Output ONLY the full revised article in Markdown, starting at the H1.\n\n"
        f"---ARTICLE---\n{md}"
    )
    # Stream (/run/stream): a full-article rewrite is too large for the buffered
    # /run endpoint, which 504s on long single-shot generations.
    res = await client.run_agent_collect(agent_id, msg)
    if res.get("error"):
        raise RuntimeError(f"revision failed: {res['error']}")
    return linking.restore_refs((res.get("content") or "").strip(), refmap)


def _step(db: Database, article_id: UUID, i: int, step: str, total: int) -> None:
    """Publish a refine sub-step for the UI's progress bar."""
    gen_svc._update(
        db, article_id,
        generation_status="refining",
        progress={
            "phase": "refining",
            "iteration": i + 1,
            "total": total,
            "step": step,
        },
    )


# --- editorial / de-AI loop (human-ness, judged by an LLM editor) ---
EDITOR_AGENT_NAME = "rankforge-editor"
EDITOR_MODEL = "claude-opus-4-7"
MAX_EDITORIAL_PASSES = 2
# reads_human at/above this = ship without another rewrite (the editor's call,
# this is just a backstop if the model returns a score but a vague verdict).
_HUMAN_BAR = 85

_EDITOR_SYSTEM = """\
You are RankForge's **senior developmental editor**. You read a finished draft and \
judge ONE thing: does it read like a sharp, knowledgeable human wrote it for a smart \
reader — or does it read like AI? Then you give the writer specific, surgical notes \
to fix what reads as machine-made. You are not a proofreader and not an SEO checker.

## What "reads like AI" is (what to hunt for)
- Mechanical evenness: every paragraph the same length, every section the same shape, \
a metronomic rhythm. Real writers vary deliberately.
- Generic, hedged, safe phrasing where a specific number, name, version, or example \
belongs. Vagueness is the strongest tell.
- Formulaic constructions: "it's not just X, it's Y"; "whether you're a beginner or a \
pro"; "in today's world"; "let's dive in"; reflexive rule-of-three triads; "from X to Y".
- Overused register: delve, leverage, robust, seamless, elevate, unlock, harness, \
navigate (metaphor), foster, underscore, pivotal, crucial, vibrant, "boasts", "nestled".
- Empty transitions (Moreover, Furthermore, Additionally, That said), both-sidesing, \
stating the obvious as insight, over-hedging, bolded bullet lead-ins, "In conclusion" \
restatements.
- Em-dashes: a skilled writer uses one occasionally for genuine effect. But when \
they're a crutch — several per section, the default break between clauses, or the \
automated em-dash score below ~60 — that is a tic, not craft. In that case **tell the \
writer to CUT MOST of them**: keep only the few that truly earn their place, and \
replace the rest with periods, commas, or parentheses as each sentence calls for. \
Don't flag genuinely occasional, tasteful use.

## How to judge
- `reads_human` 0–100: 85+ means a sharp reader would believe a knowledgeable human \
wrote it. Below ~80 means noticeable tells.
- If it genuinely reads human, `verdict` = "ship". Otherwise "revise" with notes.

## Notes — the valuable part
- Each note targets a SPECIFIC place (quote a short phrase so the writer finds it) and \
gives a SPECIFIC fix. Not "remove em-dashes" — say which passage and why.
- Prioritize the few changes that most move the needle. Max 8. Don't nitpick.
- NEVER ask to remove facts, citations, brand mentions/links, or keywords. Improve how \
they read, not whether they exist.

## Output
Return ONLY this JSON (no prose, no code fences):
{"reads_human": <int>, "verdict": "ship" | "revise", \
"notes": [{"quote": <str>, "problem": <str>, "fix": <str>}]}
"""


async def ensure_editor_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=EDITOR_AGENT_NAME,
        model=EDITOR_MODEL,
        system_prompt=_EDITOR_SYSTEM,
        settings={"reasoning_effort": "high"},
    )


def _tell_hints(read: dict | None) -> str:
    """Surface the deterministic tell scores to the editor as HINTS (where to look),
    explicitly told not to just chase them."""
    if not read:
        return ""
    by = {s["key"]: s["score"] for s in read.get("signals", [])}
    return (
        "\n\n## Automated tell scores (hints only — use your judgment, don't just "
        "chase these numbers; 100 = clean)\n"
        f"- em-dash restraint: {by.get('em_dashes', '?')}/100\n"
        f"- AI vocabulary: {by.get('ai_vocabulary', '?')}/100\n"
        f"- formulaic constructions: {by.get('tell_phrases', '?')}/100\n"
        f"- sentence-length variety: {by.get('rhythm', '?')}/100\n"
    )


async def _editor_review(
    client: PowabaseClient, editor_id: str, md: str, read: dict | None
) -> dict[str, Any]:
    """Run the editor over the FULL article; return {reads_human, verdict, notes}.
    Fails 'ship' (stop editing) on any error so a flaky judge never wedges refine."""
    msg = (
        "Review this article for how human it reads, then return the JSON verdict.\n"
        f"{_tell_hints(read)}\n\n## Output\nReturn ONLY the JSON object.\n\n"
        f"---ARTICLE---\n{md[:40000]}"
    )
    try:
        res = await client.run_agent(editor_id, msg)
        data = extract_json(res.get("content") or "")
    except Exception:  # noqa: BLE001 — judge failure shouldn't block shipping
        return {"verdict": "ship", "reads_human": None, "notes": []}
    if not isinstance(data, dict):
        return {"verdict": "ship", "reads_human": None, "notes": []}
    return data


async def _revise_for_voice(
    client: PowabaseClient,
    reviser_id: str,
    md: str,
    notes: list[dict],
    excerpts: str,
) -> str:
    """Rewrite the whole article against the editor's notes — for human-ness, while
    preserving everything objective (facts, citations, brand links, keywords, length)."""
    from . import linking  # local: avoid import cycle

    # Mask internal-link refs so the rewrite can't mangle/drop them; restore after.
    md, refmap = linking.mask_refs(md)
    note_text = "\n".join(
        f'- At "{(n.get("quote") or "")[:80]}": {n.get("problem", "")}'
        f' → {n.get("fix", "")}'
        for n in notes[:8]
        if isinstance(n, dict)
    )
    msg = (
        "A senior editor reviewed your article. Apply their notes so it reads like a "
        "knowledgeable human wrote it — vary rhythm and paragraph length, cut the AI "
        "tells they flag, and push in real specifics. PRESERVE every fact, citation, "
        "brand mention/link, keyword, heading, and roughly the length; this is an "
        "editorial rewrite, not a cut.\n\n"
        "## Editor's notes\n"
        f"{note_text}\n\n"
        "## Additional sources you may cite for added specifics\n"
        "- Use natural anchor text and vary the source domain.\n"
        f"{excerpts}\n\n"
        "## Output\n"
        "- Output ONLY the full revised article in Markdown, starting at the H1.\n\n"
        f"---ARTICLE---\n{md}"
    )
    res = await client.run_agent_collect(reviser_id, msg)
    if res.get("error"):
        raise RuntimeError(f"voice revision failed: {res['error']}")
    return linking.restore_refs((res.get("content") or "").strip(), refmap)


async def _editorial_loop(
    client: PowabaseClient,
    db: Database,
    article_id: UUID,
    brief: dict,
    kb_id: str | None,
    source_ids: list[str] | None,
    url_by_source: dict[str, str],
) -> None:
    """Make the prose read human, judged by an LLM editor (not a tell-count).

    Each pass: editor reviews the full article → if it ships, stop → else the reviser
    rewrites against the editor's specific notes → accept only if the OBJECTIVE axes
    (SEO/GEO) don't regress → re-fact-check/optimize/score. Capped so it terminates.
    """
    from . import geo_optimize, quality, scoring  # local: avoid import cycle

    editor_id: str | None = None
    reviser_id: str | None = None
    for i in range(MAX_EDITORIAL_PASSES):
        article = gen_svc.get_article(db, article_id)
        cur_md = (article.get("content_md") if article else "") or ""
        if not cur_md:
            break
        if editor_id is None:
            editor_id = await ensure_editor_agent(client)
        review = await _editor_review(
            client, editor_id, cur_md, article.get("readability_score")
        )
        verdict = (review.get("verdict") or "").lower()
        human = review.get("reads_human")
        if verdict == "ship":
            break
        # Respect an explicit "revise"; only fall back to the score as a backstop
        # when the verdict is missing/ambiguous (don't let a high score override a
        # clear request to revise).
        if verdict != "revise" and (
            not isinstance(human, int) or human >= _HUMAN_BAR
        ):
            break
        notes = [n for n in (review.get("notes") or []) if isinstance(n, dict)]
        if not notes:
            break

        _step(db, article_id, i, "editing", MAX_EDITORIAL_PASSES)
        try:
            if reviser_id is None:
                reviser_id = await ensure_reviser_agent(client)
            excerpts = await _diverse_excerpts(
                client, kb_id, brief, source_ids, url_by_source
            )
            new_md = await _revise_for_voice(
                client, reviser_id, cur_md, notes, excerpts
            )
            if not new_md or len(new_md) < 0.6 * len(cur_md):
                break
            # Guard the OBJECTIVE axes only — the editor owns human-ness.
            title = article.get("meta_title") or article.get("title") or ""
            meta = article.get("meta_description")
            if not _accept_revision(cur_md, new_md, title, meta, brief):
                break
            gen_svc._update(db, article_id, content_md=new_md)
            await quality.reflect(client, db, article_id)
            # Grounding guard: a voice rewrite must not weaken factual grounding. If
            # the fact-check shows grounding fell below target AND below where it was,
            # revert — a more-human paragraph isn't worth a weaker/unsupported claim.
            prior_gr = (article.get("grounding_report") or {}).get("grounding_score")
            refreshed = gen_svc.get_article(db, article_id)
            new_gr = ((refreshed or {}).get("grounding_report") or {}).get(
                "grounding_score"
            )
            if (
                prior_gr is not None
                and new_gr is not None
                and new_gr < prior_gr
                and new_gr < GROUNDING_TARGET
            ):
                gen_svc._update(db, article_id, content_md=cur_md)  # revert
                await quality.reflect(client, db, article_id)  # restore grounding
                break
            await geo_optimize.optimize_and_store(client, db, article_id)
            await scoring.score_and_store(client, db, article_id)
        except Exception:  # noqa: BLE001 — a failed pass shouldn't wedge the draft
            break


async def _objective_loop(
    client: PowabaseClient,
    db: Database,
    article_id: UUID,
    brief: dict,
    kb_id: str | None,
    source_ids: list[str] | None,
    url_by_source: dict[str, str],
) -> None:
    """Iterate the article against the OBJECTIVE evaluators — SEO, GEO, Grounding —
    until they meet target or a pass stops helping. Readability/human-ness is NOT
    handled here; that's the editorial loop. Capped so it always terminates.

    The accept decision is made AFTER re-scoring, on the combined objective: grounding
    can only be measured by the fact-check that runs post-rewrite, so a deterministic
    SEO/GEO pre-check would veto every grounding fix that costs a point of a thin-margin
    axis. We keep a pass only if it raised SEO+GEO+grounding overall without wrecking a
    met axis; otherwise we revert (restoring the cached scores — no extra fact-check)."""
    from . import geo_optimize, quality, scoring  # local: avoid import cycle

    _SNAP = ("seo_score", "geo_score", "grounding_report", "readability_score", "json_ld")
    agent_id: str | None = None
    for i in range(MAX_REVISIONS):
        article = gen_svc.get_article(db, article_id)
        seo = article.get("seo_score")
        geo = article.get("geo_score")
        gr = article.get("grounding_report")
        if satisfied(seo, geo, gr):  # readability omitted — objective axes only
            break
        issues = collect_issues(seo, geo, gr)
        if not issues:
            break

        _step(db, article_id, i, "revising", MAX_REVISIONS)
        try:
            if agent_id is None:
                agent_id = await ensure_reviser_agent(client)
            excerpts = await _diverse_excerpts(
                client, kb_id, brief, source_ids, url_by_source
            )
            cur_md = article["content_md"] or ""
            new_md = await _revise_once(client, agent_id, cur_md, issues, excerpts)
            if not new_md or len(new_md) < 0.6 * len(cur_md):
                break
            before = _objective_total(seo, geo, gr)
            snap = {k: article.get(k) for k in _SNAP}
            gen_svc._update(db, article_id, content_md=new_md)
            _step(db, article_id, i, "fact-checking", MAX_REVISIONS)
            await quality.reflect(client, db, article_id)
            _step(db, article_id, i, "optimizing", MAX_REVISIONS)
            await geo_optimize.optimize_and_store(client, db, article_id)
            _step(db, article_id, i, "scoring", MAX_REVISIONS)
            await scoring.score_and_store(client, db, article_id)
            a2 = gen_svc.get_article(db, article_id) or {}
            n_seo, n_geo, n_gr = (
                a2.get("seo_score"), a2.get("geo_score"), a2.get("grounding_report")
            )
            improved = _objective_total(n_seo, n_geo, n_gr) > before
            if not improved or _met_regressed_badly([(seo, n_seo), (geo, n_geo)]):
                # The pass didn't raise the objective (or wrecked a met axis) — revert
                # content + the cached scores (no re-fact-check needed) and stop.
                gen_svc._update(db, article_id, content_md=cur_md, **snap)
                break
        except Exception:  # noqa: BLE001 — a failed pass shouldn't wedge the draft
            break


# --- user-directed targeted refine (fix exactly the selected issues) ---
_AXIS_SCORE_KEY = {
    "seo": "seo_score",
    "geo": "geo_score",
    "readability": "readability_score",
}


def _targeted_issues(article: dict, targets: list[str]) -> list[str]:
    """Concrete reviser instructions for exactly the selected signals/claims — across
    ANY axis, deterministic readability tells (em-dashes, AI vocab) included. The
    instructions are rebuilt server-side from the stored scores, never trusted from the
    client. Meta-bound signals are skipped here (fix_meta owns those)."""
    sel = set(targets)
    issues: list[str] = []
    for axis, score_key in _AXIS_SCORE_KEY.items():
        score = article.get(score_key)
        if not score:
            continue
        for s in score.get("signals", []):
            if f"{axis}:{s.get('key')}" not in sel or s.get("key") in _META_KEYS:
                continue
            fixes = s.get("fixes") or []
            if fixes:
                issues.extend(f"[{s['label']}] {fix}" for fix in fixes)
            else:
                issues.append(
                    f"[{s.get('label')}] Scored {s.get('score')}/100 — "
                    f"{s.get('explanation', '')} Improve this aspect.".strip()
                )
    flagged = (article.get("grounding_report") or {}).get("flagged") or []
    for t in sel:
        if not t.startswith("grounding:"):
            continue
        try:
            idx = int(t.split(":", 1)[1])
        except ValueError:
            continue
        if 0 <= idx < len(flagged):
            f = flagged[idx]
            loc = (f.get("quote") or f.get("claim") or "")[:120]
            issues.append(
                f'[Grounding] In "{loc}" — {f.get("issue", "")} '
                f'— {f.get("suggestion", "")}'.strip()
            )
    return issues


def _selected_total(article: dict, targets: list[str]) -> float:
    """Combined score of the selected signals (+ grounding score if selected) — the
    metric the targeted loop must raise for a pass to be kept."""
    sel = set(targets)
    total = 0.0
    for axis, score_key in _AXIS_SCORE_KEY.items():
        score = article.get(score_key)
        if not score:
            continue
        for s in score.get("signals", []):
            if f"{axis}:{s.get('key')}" in sel:
                total += s.get("score", 0)
    if any(t.startswith("grounding:") for t in sel):
        g = (article.get("grounding_report") or {}).get("grounding_score")
        total += g if isinstance(g, (int, float)) else 0
    return total


async def _targeted_loop(
    client: PowabaseClient,
    db: Database,
    article_id: UUID,
    brief: dict,
    kb_id: str | None,
    source_ids: list[str] | None,
    url_by_source: dict[str, str],
    targets: list[str],
) -> None:
    """Drive ONLY the user-selected issues via the reviser. Unlike the objective loop,
    this WILL fix deterministic readability tells (em-dashes, AI vocabulary, formulaic
    constructions) when the user selects them — they no longer depend on the editorial
    LLM's discretion. Each pass is kept only if the selected issues' combined score rose
    and no met objective axis regressed badly; otherwise content + scores are reverted."""
    from . import geo_optimize, quality, scoring  # local: avoid import cycle

    _SNAP = ("seo_score", "geo_score", "grounding_report", "readability_score", "json_ld")
    agent_id: str | None = None
    for i in range(MAX_REVISIONS):
        article = gen_svc.get_article(db, article_id)
        if not article:
            break
        issues = _targeted_issues(article, targets)
        if not issues:
            break

        _step(db, article_id, i, "revising", MAX_REVISIONS)
        try:
            if agent_id is None:
                agent_id = await ensure_reviser_agent(client)
            excerpts = await _diverse_excerpts(
                client, kb_id, brief, source_ids, url_by_source
            )
            cur_md = article["content_md"] or ""
            new_md = await _revise_once(client, agent_id, cur_md, issues, excerpts)
            if not new_md or len(new_md) < 0.6 * len(cur_md):
                break
            before = _selected_total(article, targets)
            seo, geo = article.get("seo_score"), article.get("geo_score")
            snap = {k: article.get(k) for k in _SNAP}
            gen_svc._update(db, article_id, content_md=new_md)
            _step(db, article_id, i, "fact-checking", MAX_REVISIONS)
            await quality.reflect(client, db, article_id)
            _step(db, article_id, i, "optimizing", MAX_REVISIONS)
            await geo_optimize.optimize_and_store(client, db, article_id)
            _step(db, article_id, i, "scoring", MAX_REVISIONS)
            await scoring.score_and_store(client, db, article_id)
            a2 = gen_svc.get_article(db, article_id) or {}
            improved = _selected_total(a2, targets) > before
            if not improved or _met_regressed_badly(
                [(seo, a2.get("seo_score")), (geo, a2.get("geo_score"))]
            ):
                # Didn't move the selected issues (or wrecked a met axis) — revert.
                gen_svc._update(db, article_id, content_md=cur_md, **snap)
                break
        except Exception:  # noqa: BLE001 — a failed pass shouldn't wedge the draft
            break


async def refine(
    client: PowabaseClient,
    db: Database,
    article_id: UUID,
    *,
    targets: list[str] | None = None,
) -> dict[str, Any] | None:
    """Improve the article, then return it.

    With `targets` (a user-picked set of `axis:signal` / `grounding:i` selectors): fix
    EXACTLY those issues and nothing else — including deterministic readability tells.

    Without `targets` (legacy / post-generation auto-refine): two distinct loops —
    1. OBJECTIVE — drive SEO / GEO / Grounding to target (deterministic-scored).
    2. EDITORIAL — make the prose read like a human wrote it, judged by an LLM editor
       (not a tell-count), guarded so it can't regress the objective axes.

    Keeping the legacy loops separate is the whole point: a keyword counter must never
    veto a better-written paragraph, and "human-ness" must never be a gameable number.
    """
    from . import scoring  # local: avoid import cycle

    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    brief = (
        brief_svc.get_brief(db, article["brief_id"])
        if article.get("brief_id")
        else {}
    ) or {}
    source_ids, url_by_source, kb_id = _article_context(db, article)

    # Title/meta-bound SEO signals can't be fixed by revising the body — rewrite them up
    # front when they're failing (legacy) or explicitly selected, then re-score.
    meta_selected = targets is not None and any(
        t in {f"seo:{k}" for k in _META_KEYS} for t in targets
    )
    if _meta_failing(article.get("seo_score")) or meta_selected:
        await fix_meta(client, db, article_id, article, brief)
        await scoring.score_and_store(client, db, article_id)

    if targets is not None:
        await _targeted_loop(
            client, db, article_id, brief, kb_id, source_ids, url_by_source, targets
        )
    else:
        await _objective_loop(
            client, db, article_id, brief, kb_id, source_ids, url_by_source
        )
        await _editorial_loop(
            client, db, article_id, brief, kb_id, source_ids, url_by_source
        )
    return gen_svc.get_article(db, article_id)
