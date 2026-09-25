"""Frontmatter step: category, answer-first summary and FAQ for brands with a blog
profile, plus deterministic meta-length enforcement. Runs after the body is written
and on demand ("Generate summary & FAQ")."""

import logging
from typing import Any
from uuid import UUID

from ..db import Database
from ..models.blog import BlogProfile
from ..powabase import PowabaseClient
from ..util import extract_json
from . import blog_rules
from . import business_profiles as brands
from . import clusters as clusters_svc
from . import generation as gen_svc
from .agents import ensure_agent

log = logging.getLogger("rankforge.frontmatter")

AGENT_NAME = "rankforge-frontmatter"
MODEL = "claude-sonnet-4-6"
_SYSTEM = """\
You write the structured frontmatter a blog post needs for search and AI answer \
engines: a category, a short answer-first summary, and an FAQ. You return only JSON.
"""


def _prompt(article: dict, brand_name: str, profile: BlogProfile) -> str:
    cats = "\n".join(
        f"- {c.key}: {c.label} ({c.description})" for c in profile.categories
    )
    stance = (
        f"- Never state a {brand_name} gap, limitation or missing feature in the "
        "summary or FAQ. Position it as equal to or ahead of alternatives.\n"
        if profile.stance == "favor_brand" else ""
    )
    return (
        f"Write frontmatter for this {brand_name} blog post.\n\n"
        "## Categories (pick exactly one key)\n"
        f"{cats}\n\n"
        "## Rules\n"
        f"- summary: {profile.summary.min_words}-{profile.summary.max_words} words. "
        "Its first sentence directly answers the question the title asks. "
        f"Self-contained. Names {brand_name} where the post discusses it.\n"
        f"- faq: {profile.faq.min}-{profile.faq.max} items. Each q is phrased the way "
        "someone would search it; each a answers first, at most 60 words. At least "
        f'one q is "How does {brand_name} handle …?".\n'
        f"{stance}\n"
        "## Output\n"
        'Return ONLY {"category": str, "summary": str, '
        '"faq": [{"q": str, "a": str}]}\n\n'
        f"Title: {article.get('title') or ''}\n\n"
        f"---ARTICLE---\n{(article.get('content_md') or '')[:16000]}"
    )


async def _ask(client: PowabaseClient, msg: str) -> dict[str, Any]:
    agent_id = await ensure_agent(
        client, name=AGENT_NAME, model=MODEL, system_prompt=_SYSTEM,
        settings={"temperature": 0.2},
    )
    res = await client.run_agent(agent_id, msg)
    return extract_json(res.get("content") or "") or {}


def _cluster_category(db: Database, article: dict) -> str | None:
    cid = article.get("cluster_id")
    if not cid:
        return None
    cl = clusters_svc.get_cluster(db, cid)
    return (cl or {}).get("category")


async def generate(
    client: PowabaseClient, db: Database, article_id: UUID
) -> list[str]:
    """Write category/summary/FAQ. Returns flags that still block export; [] when the
    brand has no profile (nothing written)."""
    article = gen_svc.get_article(db, article_id)
    if not article or not article.get("business_id"):
        return []
    brand = brands.get_profile(db, article["business_id"])
    profile = blog_rules.profile_of(brand)
    if not profile:
        return []
    name = (brand or {}).get("name") or "the brand"
    cc = _cluster_category(db, article)
    msg = _prompt(article, name, profile)
    clean, flags = blog_rules.validate_frontmatter(
        await _ask(client, msg), profile, cluster_category=cc
    )
    if flags:
        retry = (
            f"{msg}\n\n## Your previous answer had these problems — fix them\n"
            + "\n".join(f"- {f}" for f in flags)
        )
        clean, flags = blog_rules.validate_frontmatter(
            await _ask(client, retry), profile, cluster_category=cc
        )
    gen_svc._update(db, article_id, **clean)
    return flags


def enforce_meta(
    db: Database, article_id: UUID, article: dict, profile: BlogProfile
) -> None:
    """Deterministic backstop: meta_title/description within the profile limits. A
    long H1 always gets a meta_title (derived from the title if the model gave none)."""
    tmax, dmax = profile.meta.title_max, profile.meta.description_max
    title = article.get("title") or ""
    mt = (article.get("meta_title") or "").strip()
    fields: dict[str, Any] = {}
    if len(title) > tmax or (mt and len(mt) > tmax):
        new_mt = blog_rules.clamp_chars(mt or title, tmax)
        if new_mt != mt:
            fields["meta_title"] = new_mt
    desc = article.get("meta_description") or ""
    if len(desc) > dmax:
        fields["meta_description"] = blog_rules.clamp_chars(desc, dmax)
    if fields:
        gen_svc._update(db, article_id, **fields)


async def complete(
    client: PowabaseClient, db: Database, article_id: UUID
) -> list[str]:
    """Meta (model, then clamp) + category/summary/FAQ, and drop a body FAQ section
    (the FAQ lives in frontmatter). Used after generation and by the "Generate
    summary & FAQ" / "Fix automatically" actions. The prior state is versioned first,
    so hand edits it overwrites can be reverted."""
    from . import brief as brief_svc
    from . import revise

    article = gen_svc.get_article(db, article_id)
    if not article or not article.get("business_id"):
        return []
    profile = blog_rules.profile_of(brands.get_profile(db, article["business_id"]))
    if not profile:
        return []
    gen_svc.snapshot_version(db, article)
    brief = (
        brief_svc.get_brief(db, article["brief_id"]) if article.get("brief_id") else {}
    ) or {}
    await revise.fix_meta(
        client, db, article_id, article, brief,
        title_max=profile.meta.title_max, description_max=profile.meta.description_max,
    )
    fresh = gen_svc.get_article(db, article_id) or article
    enforce_meta(db, article_id, fresh, profile)
    flags = await generate(client, db, article_id)
    if profile.faq.enabled:
        md = (gen_svc.get_article(db, article_id) or {}).get("content_md") or ""
        if blog_rules.BODY_FAQ_RE.search(md):
            gen_svc._update(db, article_id, content_md=blog_rules.strip_body_faq(md))
    return flags
