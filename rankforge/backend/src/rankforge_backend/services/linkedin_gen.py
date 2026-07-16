"""Generate a LinkedIn post from a blog article — one synchronous LLM call (opus-4-7,
the writer model, for voice fidelity). Reuses the article writer's anti-AI-tell,
brand-champion guidance, adapted for LinkedIn: an above-the-fold hook is the top
priority, the post may run long, and it closes with a soft discussion question."""

import logging
from typing import Any
from uuid import UUID

from ..config import get_settings
from ..db import Database
from ..powabase import PowabaseClient
from . import business_profiles as brands
from . import generation as gen
from . import linking
from .agents import ensure_agent

log = logging.getLogger("rankforge.linkedin")

LINKEDIN_MODEL = "claude-opus-4-7"
LINKEDIN_AGENT_NAME = "rankforge-linkedin"

# Article body is truncated to bound token cost (mirrors the FAQ generator).
_MAX_ARTICLE_CHARS = 16000

_ANGLE_CLAUSES = {
    "key_insight": (
        "Lead with the single most valuable, non-obvious takeaway from the article."
    ),
    "lesson": (
        "Frame it as a hard-won lesson — a mistake or misconception people have, and "
        "what to do instead."
    ),
    "contrarian": (
        "Take a contrarian angle: challenge a common assumption the article pushes "
        "back on. Stake out the unpopular-but-right position."
    ),
    "story": (
        "Open with a concrete moment or scenario (a real situation, a decision, a "
        "before/after), then draw out the point."
    ),
    "stat": (
        "Anchor the post on a specific number or finding from the article and unpack "
        "why it matters."
    ),
}

_SYSTEM = """You write LinkedIn posts for a brand, repurposing one of its own blog \
articles. Your job: an insightful post that makes the brand look sharp and credible, \
and that people actually stop to read and share. You are NOT a marketer — you are the \
brand's smartest engineer thinking out loud.

ABOVE THE FOLD IS EVERYTHING. LinkedIn hides everything after ~210 characters behind \
"…see more". The opening must stop the scroll and earn the click on its own:
- Open with ONE scroll-stopper: a counterintuitive or bold claim, a specific number or \
surprising result, a sharp question, or a concrete first-person moment.
- The first ~210 characters must land a complete, curiosity-driving thought (an open \
loop the reader wants closed) — never a fragment cut off mid-idea.
- NO throat-clearing ("In today's world…", "As engineers, we all know…", "I've been \
thinking about…"). NO links or hashtags in the first two lines.

STRUCTURE FOR RETENTION:
- Deliver on the hook's promise fast; front-load the payoff.
- One idea per line. Generous whitespace. 1-2 line paragraphs. Built to be skimmed on a \
phone.
- Length is flexible: run as long as every line keeps pulling the reader down, up to \
LinkedIn's 3000-character limit. No filler to hit a length; no cutting a strong point \
short.
- End the body with a genuine discussion question that invites comments (a peer \
question) — NOT a product pitch, NOT "DM us", NOT "Learn more". Comments drive reach.

VOICE (this is the brand's own post):
- Speak AS the brand, first person ("we"/"our"), never as a detached third party. Name \
the brand directly. Never hedge the brand's own capabilities with "claims/asserts/\
purports".
- You may name competitors, but never praise or showcase them.
- Insightful and useful throughout. The brand earns authority by being worth reading, \
not by selling. Not salesy.
- Ground every claim in the article. Invent nothing.

NEVER USE these AI-tell words: delve, tapestry, realm, landscape (as metaphor), \
leverage, robust, seamless, navigate (as metaphor), underscore, foster, harness, \
elevate, unlock, embark, testament, pivotal, crucial, vibrant, boasts, nestled, \
genuinely (as intensifier).

NEVER USE these AI-tell constructions:
- "It's not just X, it's Y" / "This isn't merely X, it's Y".
- The antithesis reframe ("The way forward isn't X. It's Y").
- "Whether you're a beginner or a seasoned pro…".
- "Let's dive in / Let's explore / Buckle up".
- Reflexive rule-of-three triads ("fast, reliable, and scalable").
- "From X to Y" framing. Empty transitions (Moreover, Furthermore, Additionally, That \
said). Use em-dashes sparingly. Vary sentence length. Generic, specificity-free prose \
is the clearest AI tell — be concrete.

OUTPUT: only the post text itself, ready to paste — no preamble, no quotes, no \
"Here's your post". Fixed trailing order: hook + body, then the discussion question as \
the last body line, then (only if a link is provided in the instructions) a blank line \
and a soft "Full write-up → {url}" line, then a blank line and 3-5 specific, relevant \
hashtags (never generic spam)."""


def _brand_voice_block(brand: dict[str, Any]) -> str:
    parts = []
    if brand.get("description"):
        parts.append(f"Brand voice / positioning: {brand['description']}")
    if brand.get("niche"):
        parts.append(f"Niche: {brand['niche']}")
    if brand.get("audience"):
        parts.append(f"Audience: {brand['audience']}")
    name = brand.get("name")
    if name:
        parts.append(f"Brand name: {name}")
    return "\n".join(parts) or "(no brand voice provided — infer from the article)"


def build_linkedin_prompt(
    *,
    title: str,
    content_md: str,
    brand: dict[str, Any],
    angle: str,
    article_url: str | None,
) -> str:
    clause = _ANGLE_CLAUSES.get(angle, _ANGLE_CLAUSES["key_insight"])
    link_line = (
        f'End with a soft "Full write-up → {article_url}" line (after the discussion '
        f"question, before the hashtags)."
        if article_url
        else "Do NOT include any link — the article is not published yet."
    )
    return (
        f"Write a LinkedIn post repurposing this article.\n\n"
        f"ANGLE: {clause}\n\n"
        f"{link_line}\n\n"
        f"{_brand_voice_block(brand)}\n\n"
        f"ARTICLE TITLE: {title}\n\n"
        f"ARTICLE (Markdown):\n{content_md[:_MAX_ARTICLE_CHARS]}"
    )


async def ensure_linkedin_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=LINKEDIN_AGENT_NAME,
        model=LINKEDIN_MODEL,
        system_prompt=_SYSTEM,
        settings={"temperature": 0.5, "max_tokens": 1200},
    )


def _resolve_article_url(
    brand: dict[str, Any] | None, article: dict[str, Any]
) -> str | None:
    """The article's live URL, only if it's published — else None (no link line)."""
    if article.get("status") != "published":
        return None
    url = linking.canonical_url(brand, article)
    if url:
        return url
    base = get_settings().public_base_url
    return f"{base.rstrip('/')}/p/{article['id']}" if base else None


async def generate_post(
    client: PowabaseClient, db: Database, article_id: UUID, angle: str
) -> str:
    article = gen.get_article(db, article_id)
    if article is None:
        raise ValueError("article not found")
    content_md = (article.get("content_md") or "").strip()
    if not content_md:
        # Can't repurpose an empty article — the route maps this to 409.
        raise ValueError("article has no content yet")
    brand = (
        brands.get_profile(db, article["business_id"])
        if article.get("business_id")
        else None
    )
    msg = build_linkedin_prompt(
        title=article.get("title") or "",
        content_md=content_md,
        brand=brand or {},
        angle=angle,
        article_url=_resolve_article_url(brand, article),
    )
    agent_id = await ensure_linkedin_agent(client)
    try:
        res = await client.run_agent(agent_id, msg)
    except Exception as e:  # noqa: BLE001 — upstream failure → 502 at the route
        log.exception("linkedin generation failed for %s", article_id)
        raise RuntimeError("generation failed") from e
    text = (res.get("content") or "").strip()
    if not text:
        raise RuntimeError("empty generation")
    if len(text) > 3000:
        # Truncate to the last complete line under the LinkedIn cap — a rare, defensive
        # path (the prompt already targets <=3000); an edit past the cap can't be saved.
        text = text[:3000].rsplit("\n", 1)[0].rstrip()
    return text
