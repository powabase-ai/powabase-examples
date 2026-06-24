"""SEO + GEO scoring — hybrid (deterministic signals + one LLM judgment for the
fuzzy GEO bits). Advisory: scores are stored on the article and shown in the editor;
they never block publish.

Score object: { total, target, met, signals: [
    { key, label, score, weight, explanation, fixes: [...], method } ] }
"""

import re
from typing import Any
from urllib.parse import urlparse

from ..db import Database
from ..powabase import PowabaseClient
from ..util import extract_json
from . import brief as brief_svc
from . import generation as gen_svc
from . import templates as templates_svc
from .agents import ensure_agent

SEO_TARGET = 80
GEO_TARGET = 85
READABILITY_TARGET = 80

# AI "tells" — the register/constructions search engines now penalize as machine-
# written. Detected deterministically (density matters more than any single use).
_AI_WORDS = (
    "delve", "delved", "delving", "tapestry", "realm", "realms", "landscape",
    "leverage", "leverages", "leveraging", "leveraged", "robust", "seamless",
    "seamlessly", "navigate", "navigating", "underscore", "underscores",
    "underscoring", "foster", "fosters", "fostering", "harness", "harnessing",
    "elevate", "elevates", "elevating", "unlock", "unlocks", "unlocking",
    "embark", "embarking", "testament", "pivotal", "crucial", "vibrant",
    "boasts", "boasting", "nestled",
)
_AI_WORD_RE = re.compile(r"(?<![a-z])(?:" + "|".join(_AI_WORDS) + r")(?![a-z])", re.I)
_TELL_RE = re.compile(
    r"it'?s not (?:just|merely)\b"
    r"|\bisn'?t (?:just|merely)\b"
    r"|\bwhether you'?re an?\b"
    r"|\bin today'?s\b.{0,40}?\b(?:world|landscape|era|age)\b"
    r"|\blet'?s (?:dive in|explore|take a look|unpack)\b"
    r"|\bbuckle up\b"
    r"|\bin conclusion\b"
    r"|\bat the end of the day\b",
    re.I,
)
_EMPTY_TRANSITION_RE = re.compile(
    r"(?<![a-z])(?:moreover|furthermore|additionally|that said)(?![a-z])", re.I
)
_BOLD_BULLET_RE = re.compile(r"^\s*[-*]\s+\*\*[^*]+\*\*\s*[:\-—]", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+\S", re.MULTILINE)


# ---------- text helpers (deterministic) ----------
def _clean(md: str) -> str:
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)  # links → anchor text
    t = re.sub(r"`{1,3}[^`]*`{1,3}", " ", t)  # code
    t = re.sub(r"[#>*_~|-]", " ", t)
    return t


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[.!?]+", text) if s.strip()]


def _syllables(word: str) -> int:
    word = word.lower()
    n = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e"):
        n -= 1
    return max(1, n)


def _flesch(text: str) -> float:
    words, sents = _words(text), _sentences(text)
    if not words or not sents:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / len(sents)) - 84.6 * (syl / len(words))


def _headings(md: str) -> list[tuple[int, str]]:
    return [
        (len(m.group(1)), m.group(2).strip())
        for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", md, re.MULTILINE)
    ]


def _links(md: str) -> list[str]:
    return re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", md)


def _signal(key, label, score, weight, explanation, fixes, method="deterministic"):
    return {
        "key": key,
        "label": label,
        "score": int(round(max(0, min(100, score)))),
        "weight": weight,
        "explanation": explanation,
        "fixes": fixes,
        "method": method,
    }


def _aggregate(signals: list[dict], target: int) -> dict[str, Any]:
    tw = sum(s["weight"] for s in signals) or 1
    total = round(sum(s["score"] * s["weight"] for s in signals) / tw)
    return {"total": total, "target": target, "met": total >= target, "signals": signals}


def _band(value: float, lo: float, hi: float, slack: float) -> float:
    """100 inside [lo,hi]; linear falloff to 0 over `slack` beyond each edge."""
    if lo <= value <= hi:
        return 100.0
    d = (lo - value) if value < lo else (value - hi)
    return max(0.0, 100.0 * (1 - d / slack))


# ---------- SEO ----------
def score_seo(content_md: str, title: str, meta: str | None, brief: dict) -> dict:
    text = _clean(content_md)
    words = _words(text)
    wc = len(words)
    lower = text.lower()
    pk = (brief.get("primary_keyword") or "").strip().lower()
    secondary = [s.lower() for s in (brief.get("secondary_keywords") or [])]
    headings = _headings(content_md)
    h_levels = [lv for lv, _ in headings]
    links = _links(content_md)
    title = title or ""
    meta = meta or ""

    sig = []

    in_title = bool(pk) and pk in title.lower()
    sig.append(_signal(
        "keyword_title", "Primary keyword in title", 100 if in_title else 0, 0.12,
        f'"{pk}" {"appears in" if in_title else "is missing from"} the title.',
        [] if in_title else ["Work the primary keyword into the title naturally."]))

    early = bool(pk) and pk in " ".join(words[:100]).lower()
    sig.append(_signal(
        "keyword_early", "Keyword in the opening", 100 if early else 0, 0.08,
        "Primary keyword appears early." if early else "Primary keyword not in the first 100 words.",
        [] if early else ["Mention the primary keyword in the intro."]))

    # Non-word lookarounds (not \b) so punctuation keywords like "c++"/".net" match.
    pk_hits = (
        len(re.findall(rf"(?<![a-z0-9]){re.escape(pk)}(?![a-z0-9])", lower)) if pk else 0
    )
    density = (pk_hits / wc * 100) if (pk and wc) else 0
    sig.append(_signal(
        "keyword_density", "Keyword density", _band(density, 0.4, 2.5, 1.5), 0.10,
        f"Primary keyword density ~{density:.2f}%.",
        ["Aim for 0.4–2.5% density."] if not 0.4 <= density <= 2.5 else []))

    present = sum(1 for s in secondary if s and s in lower)
    cov = (present / len(secondary) * 100) if secondary else 100
    sig.append(_signal(
        "secondary_coverage", "Secondary keyword coverage", cov, 0.12,
        f"{present}/{len(secondary)} secondary keywords used.",
        ["Cover more of the brief's secondary keywords."] if cov < 70 else []))

    h1 = h_levels.count(1)
    h2 = h_levels.count(2)
    skips = sum(
        1 for a, b in zip(h_levels, h_levels[1:], strict=False) if b - a > 1
    )
    hs = 100 - (0 if h1 == 1 else 30) - (0 if h2 >= 3 else 20) - min(40, skips * 15)
    sig.append(_signal(
        "heading_structure", "Heading hierarchy", hs, 0.14,
        f"{h1} H1, {h2} H2, {skips} skipped level(s).",
        [f for f, c in [("Use exactly one H1.", h1 != 1),
                        ("Add more H2 sections.", h2 < 3),
                        ("Don't skip heading levels.", skips > 0)] if c]))

    tl = len(title)
    sig.append(_signal(
        "title_length", "Title length", _band(tl, 30, 60, 25), 0.10,
        f"Title is {tl} characters.",
        ["Target 30–60 characters."] if not 30 <= tl <= 60 else []))

    ml = len(meta)
    sig.append(_signal(
        "meta_length", "Meta description length", _band(ml, 120, 160, 60), 0.10,
        f"Meta description is {ml} characters.",
        ["Target 120–160 characters."] if not 120 <= ml <= 160 else []))

    target_wc = brief.get("target_word_count") or wc
    ratio = wc / target_wc if target_wc else 1
    sig.append(_signal(
        "word_count", "Length vs target", _band(ratio, 0.85, 1.3, 0.5), 0.10,
        f"{wc} words vs ~{target_wc} target.",
        ["Expand toward the target length."] if ratio < 0.85 else []))

    ext = len(links)
    sig.append(_signal(
        "external_links", "Outbound citations", _band(ext, 3, 60, 6), 0.08,
        f"{ext} outbound link(s).",
        ["Cite a few authoritative sources."] if ext < 3 else []))

    fl = _flesch(text)
    sig.append(_signal(
        "readability", "Readability (Flesch)", _band(fl, 45, 70, 30), 0.06,
        f"Flesch reading ease ~{fl:.0f}.",
        ["Shorten sentences for easier reading."] if fl < 45 else []))

    return _aggregate(sig, SEO_TARGET)


# ---------- GEO ----------
def score_geo(
    content_md: str,
    brief: dict,
    llm: dict | None,
    has_structured_data: bool = False,
    geo_target: int = GEO_TARGET,
) -> dict:
    text = _clean(content_md)
    wc = len(_words(text)) or 1
    lower = text.lower()
    links = _links(content_md)
    domains = {urlparse(u).netloc.replace("www.", "") for u in links}
    entities = [e.lower() for e in (brief.get("entities") or [])]
    questions = brief.get("questions") or []

    sig = []

    per_1k = len(links) / wc * 1000
    sig.append(_signal(
        "citation_density", "Citable-claim density", _band(per_1k, 4, 40, 8), 0.16,
        f"{per_1k:.1f} citations per 1,000 words.",
        ["Attribute more claims to sources."] if per_1k < 4 else []))

    sig.append(_signal(
        "source_authority", "Source diversity", _band(len(domains), 3, 40, 6), 0.10,
        f"{len(domains)} distinct source domain(s).",
        ["Cite a wider range of authoritative sources."] if len(domains) < 3 else []))

    e_present = sum(1 for e in entities if e and e in lower)
    e_cov = (e_present / len(entities) * 100) if entities else 100
    sig.append(_signal(
        "entity_coverage", "Entity coverage", e_cov, 0.16,
        f"{e_present}/{len(entities)} must-cover entities present.",
        ["Cover the remaining brief entities."] if e_cov < 70 else []))

    def _answered(q: str) -> bool:
        kw = [w for w in _words(q.lower()) if len(w) > 3]
        return bool(kw) and sum(1 for w in kw if w in lower) / len(kw) >= 0.6

    q_ans = sum(1 for q in questions if _answered(q))
    q_cov = (q_ans / len(questions) * 100) if questions else 100
    sig.append(_signal(
        "question_coverage", "Question coverage", q_cov, 0.16,
        f"{q_ans}/{len(questions)} brief questions appear addressed.",
        ["Answer the remaining PAA-style questions."] if q_cov < 70 else []))

    sig.append(_signal(
        "structured_data", "Structured data (JSON-LD)",
        100 if has_structured_data else 0, 0.10,
        "schema.org JSON-LD present." if has_structured_data else "No schema.org JSON-LD yet.",
        [] if has_structured_data else ["Run GEO optimize to emit Article/FAQPage JSON-LD."]))

    lists = len(re.findall(r"^\s*([-*]|\d+\.)\s+", content_md, re.MULTILINE))
    tables = content_md.count("\n|")
    extract = _band(lists, 3, 200, 6) * 0.6 + (100 if tables else 40) * 0.4
    sig.append(_signal(
        "extractability", "Extractable formatting", extract, 0.12,
        f"{lists} list item(s), {'a' if tables else 'no'} table(s).",
        ["Add lists/tables answer engines can lift."] if lists < 3 else []))

    # Always include the LLM-judged signals so the weight denominator (and thus the
    # target's meaning) is the same whether or not the judge ran. When it's
    # unavailable, they default to a neutral 50 rather than being dropped — which
    # would otherwise re-normalize the other signals and shift the effective target.
    _NEUTRAL = "Not evaluated (LLM judge unavailable)."
    sig.append(_signal(
        "direct_answer", "Direct-answer leads",
        llm.get("direct_answer", 0) if llm else 50, 0.12,
        llm.get("direct_answer_note", "LLM judgment of extractable lead answers.")
        if llm else _NEUTRAL,
        llm.get("direct_answer_fixes", []) if llm else [], method="llm"))
    sig.append(_signal(
        "citability", "Claim citability",
        llm.get("citability", 0) if llm else 50, 0.08,
        llm.get("citability_note", "LLM judgment of how quotable/specific claims are.")
        if llm else _NEUTRAL,
        llm.get("citability_fixes", []) if llm else [], method="llm"))

    return _aggregate(sig, geo_target)


# ---------- Readability (does it read human, not machine-generated?) ----------
def score_readability(content_md: str, llm: dict | None) -> dict:
    """Hybrid: deterministic AI-tell detection + an LLM 'human voice' judgment.
    Search engines penalize content that reads as AI-generated, so this is a
    first-class scored dimension alongside SEO/GEO/Grounding."""
    text = _clean(content_md)
    words = _words(text)
    wc = len(words) or 1
    sents = _sentences(text)

    sig: list[dict] = []

    ai_hits = len(_AI_WORD_RE.findall(content_md))
    ai_density = ai_hits / wc * 1000
    ai_score = max(0.0, 100.0 * (1 - max(0.0, ai_density - 1.5) / 7.5))
    sig.append(_signal(
        "ai_vocabulary", "AI-tell vocabulary", ai_score, 0.18,
        f"{ai_hits} flagged word(s) (~{ai_density:.1f}/1k) from the "
        "delve/leverage/robust/seamless/elevate register.",
        ["Swap the flagged words for plain language; never stack several in a "
         "paragraph."] if ai_score < 80 else []))

    tell_hits = len(_TELL_RE.findall(content_md))
    tell_score = max(0.0, 100.0 - tell_hits * 25)
    sig.append(_signal(
        "tell_phrases", "Formulaic constructions", tell_score, 0.16,
        f"{tell_hits} AI-tell construction(s) (\"it's not just X…\", \"whether "
        "you're a…\", \"in today's… world\", \"let's dive in\", \"in conclusion\").",
        ["Rewrite the formulaic openers/closers in a natural voice."]
        if tell_hits else []))

    em = content_md.count("—")
    em_density = em / wc * 1000
    sig.append(_signal(
        "em_dashes", "Em-dash restraint", _band(em_density, 0, 3, 5), 0.10,
        f"{em} em-dash(es) (~{em_density:.1f}/1k).",
        ["Thin out em-dashes; prefer commas, periods, or parentheses."]
        if em_density > 3 else []))

    et = len(_EMPTY_TRANSITION_RE.findall(text))
    sig.append(_signal(
        "transitions", "Natural transitions", max(0.0, 100.0 - et * 12), 0.08,
        f"{et} filler transition(s) (Moreover / Furthermore / Additionally / "
        "That said).",
        ["Cut the filler transitions; let the sentences connect directly."]
        if et else []))

    sl = [len(_words(s)) for s in sents]
    mean_sl = (sum(sl) / len(sl)) if sl else 0
    if len(sl) > 1 and mean_sl:
        var = sum((x - mean_sl) ** 2 for x in sl) / len(sl)
        cv = (var ** 0.5) / mean_sl  # coefficient of variation
    else:
        cv = 0.0
    sig.append(_signal(
        "rhythm", "Sentence-length variety", _band(cv, 0.5, 2.0, 0.5), 0.12,
        f"Sentence length varies with CV ~{cv:.2f}; mechanical evenness reads "
        "as machine-made.",
        ["Mix short and long sentences; avoid uniform sentence/paragraph length."]
        if cv < 0.5 else []))

    bullets = len(_BULLET_RE.findall(content_md))
    bolded = len(_BOLD_BULLET_RE.findall(content_md))
    frac = (bolded / bullets) if bullets else 0.0
    sig.append(_signal(
        "bullet_style", "Bullet style",
        100 if frac < 0.5 else max(0.0, 100.0 - (frac - 0.5) * 200), 0.06,
        f"{bolded}/{bullets} bullets use a bolded lead-in." if bullets
        else "No bullet lists.",
        ["Don't bold the lead-in of every bullet; use prose where items aren't "
         "truly parallel."] if frac >= 0.5 else []))

    # Always-include LLM signals (neutral 50 when the judge is unavailable) so the
    # weight denominator — and the target's meaning — is stable.
    _NEUTRAL = "Not evaluated (LLM judge unavailable)."
    sig.append(_signal(
        "human_voice", "Human voice",
        llm.get("human_voice", 0) if llm else 50, 0.18,
        llm.get("human_voice_note", "LLM judgment of how human the writing reads.")
        if llm else _NEUTRAL,
        llm.get("human_voice_fixes", []) if llm else [], method="llm"))
    sig.append(_signal(
        "flow", "Flow & specificity",
        llm.get("flow", 0) if llm else 50, 0.12,
        llm.get("flow_note", "LLM judgment of smoothness, rhythm, and specificity.")
        if llm else _NEUTRAL,
        llm.get("flow_fixes", []) if llm else [], method="llm"))

    return _aggregate(sig, READABILITY_TARGET)


JUDGE_AGENT_NAME = "rankforge-geo-judge"
# Evaluation/judgment with short JSON output — top model + extended thinking.
JUDGE_MODEL = "claude-opus-4-7"
_JUDGE_SYSTEM = """\
You are a **GEO (Generative Engine Optimization) auditor**. You rate how easily an \
AI answer engine could lift and cite an article, and you return only structured JSON.

## Output discipline
- Return exactly one JSON object — no prose, no commentary, no code fences.
"""
_JUDGE_PROMPT = """\
Rate the article on two independent axes (0–100 each) for how well an AI answer \
engine could lift and cite it.

## Axes
- **direct_answer** — does each section open with a concise, self-contained, \
extractable answer?
- **citability** — are claims specific, quotable, and attributed to sources?

## For each axis return
- The score (0–100).
- A one-line note explaining the score.
- A short list of concrete fixes (empty when none are needed).

## Output
Return ONLY this JSON object:
{"direct_answer": int, "direct_answer_note": str, "direct_answer_fixes": [str], \
"citability": int, "citability_note": str, "citability_fixes": [str]}\
"""

async def ensure_judge_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=JUDGE_AGENT_NAME,
        model=JUDGE_MODEL,
        system_prompt=_JUDGE_SYSTEM,
        settings={"reasoning_effort": "high"},
    )


async def judge_geo(client: PowabaseClient, content_md: str) -> dict | None:
    try:
        agent_id = await ensure_judge_agent(client)
        res = await client.run_agent(
            agent_id, f"{_JUDGE_PROMPT}\n\n---ARTICLE---\n{content_md[:16000]}"
        )
        return extract_json(res.get("content") or "")
    except Exception:  # noqa: BLE001 — scoring degrades to deterministic-only
        return None


READ_JUDGE_AGENT_NAME = "rankforge-readability-judge"
READ_JUDGE_MODEL = "claude-opus-4-7"
_READ_JUDGE_SYSTEM = """\
You are a **human-writing auditor**. Search engines now penalize content that reads \
as AI-generated, so you rate how human and natural an article reads. You return only \
structured JSON.

## Output discipline
- Return exactly one JSON object — no prose, no commentary, no code fences.
"""
_READ_JUDGE_PROMPT = """\
Rate the article on two axes (0–100 each) for how HUMAN it reads. High means a \
knowledgeable person clearly wrote it; low means it reads as machine-generated.

## Axes
- human_voice — a real point of view, confident unqualified claims, and concrete \
specificity (numbers, names, dates, examples). Penalize: the AI register (delve, \
leverage, robust, seamless, navigate, elevate, unlock, pivotal, crucial, vibrant, \
boasts, nestled…); formulaic constructions ("it's not just X, it's Y"; "whether \
you're a beginner or a pro"; "in today's fast-paced world"; "let's dive in"; "in \
conclusion"); and generic, hedge-everything, specificity-free prose.
- flow — natural rhythm (a mix of short and long sentences, uneven section lengths) \
that reads smoothly, NOT the mechanical evenness and over-even paragraphing of \
machine text. Penalize excessive em-dashes and filler transitions (moreover, \
furthermore, additionally, that said).

## For each axis return
- The score (0–100).
- A one-line note explaining the score.
- A short list of concrete fixes (empty when none are needed).

## Output
Return ONLY this JSON object:
{"human_voice": int, "human_voice_note": str, "human_voice_fixes": [str], \
"flow": int, "flow_note": str, "flow_fixes": [str]}\
"""


async def ensure_read_judge_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=READ_JUDGE_AGENT_NAME,
        model=READ_JUDGE_MODEL,
        system_prompt=_READ_JUDGE_SYSTEM,
        settings={"reasoning_effort": "high"},
    )


async def judge_readability(client: PowabaseClient, content_md: str) -> dict | None:
    try:
        agent_id = await ensure_read_judge_agent(client)
        res = await client.run_agent(
            agent_id, f"{_READ_JUDGE_PROMPT}\n\n---ARTICLE---\n{content_md[:16000]}"
        )
        return extract_json(res.get("content") or "")
    except Exception:  # noqa: BLE001 — scoring degrades to deterministic-only
        return None


async def score_and_store(
    client: PowabaseClient, db: Database, article_id
) -> dict[str, Any] | None:
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    brief = brief_svc.get_brief(db, article["brief_id"]) if article.get("brief_id") else {}
    brief = brief or {}
    md = article.get("content_md") or ""

    seo = score_seo(md, article.get("meta_title") or article.get("title") or "",
                    article.get("meta_description"), brief)
    llm = await judge_geo(client, md)
    template = templates_svc.get_template(db, brief.get("article_type"))
    geo = score_geo(
        md, brief, llm,
        has_structured_data=bool(article.get("json_ld")),
        geo_target=template["geo_target"] if template else GEO_TARGET,
    )
    read_llm = await judge_readability(client, md)
    readability = score_readability(md, read_llm)

    gen_svc._update(
        db, article_id, seo_score=seo, geo_score=geo, readability_score=readability
    )
    return {"seo_score": seo, "geo_score": geo, "readability_score": readability}
