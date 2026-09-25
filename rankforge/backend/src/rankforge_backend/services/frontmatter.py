"""Frontmatter step: category, answer-first summary and FAQ for brands with a blog
profile, plus deterministic meta-length enforcement. Runs after the body is written
and on demand ("Generate summary & FAQ")."""

import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from ..db import Database
from ..models.blog import BlogProfile
from ..powabase import PowabaseClient
from ..util import extract_json
from . import blog_rules, prose_style
from . import business_profiles as brands
from . import clusters as clusters_svc
from . import generation as gen_svc
from .agents import ensure_agent

log = logging.getLogger("rankforge.frontmatter")

AGENT_NAME = "rankforge-frontmatter"
MODEL = "claude-sonnet-4-6"
# The summary and FAQ are published prose, so they get the article writer's
# human-voice rules (prose_style is the single source of truth). A change here reaches
# the running Powabase agent on the next process start: ensure_agent refreshes an
# existing agent's system prompt the first time it provisions it.
_SYSTEM = """\
You write the structured frontmatter a blog post needs for search and AI answer \
engines: a category, a short answer-first summary, and an FAQ. You return only JSON.

## Write like a human, not an AI
The summary and every FAQ question and answer are read by people. Editors reject \
copy that reads as machine-written, so steer clear of all of these:

""" + prose_style.writer_block() + """

- Do not use em-dashes (—); use a comma, period, or parentheses instead.
- Cut empty transitions: Moreover, Furthermore, Additionally, That said.
- Use concrete specifics from the post (numbers, names, versions) over smooth \
generalities, and make confident claims the post supports instead of hedging.
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


async def _ask(client: PowabaseClient, msg: str) -> dict[str, Any] | None:
    """The model's JSON reply, or None when it isn't a JSON object (a failed
    attempt — never a 500, and never written)."""
    agent_id = await ensure_agent(
        client, name=AGENT_NAME, model=MODEL, system_prompt=_SYSTEM,
        settings={"temperature": 0.2},
    )
    res = await client.run_agent(agent_id, msg)
    try:
        data = extract_json(res.get("content") or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _cluster_category(db: Database, article: dict) -> str | None:
    cid = article.get("cluster_id")
    if not cid:
        return None
    cl = clusters_svc.get_cluster(db, cid)
    return (cl or {}).get("category")


FM_FIELDS = ("category", "summary", "faq")
_UNPARSEABLE = "the model's reply was not valid JSON"


def field_ok(field: str, value: Any, profile: BlogProfile) -> bool:
    """Whether one frontmatter value passes the blog's export rules."""
    if field == "category":
        return value in {c.key for c in profile.categories}
    if field == "summary":
        n = blog_rules.word_count(value)
        return profile.summary.min_words <= n <= profile.summary.max_words
    if field == "faq":
        return profile.faq.min <= len(blog_rules.clean_faq(value)) <= profile.faq.max
    return False


def _present(value: Any) -> bool:
    """Whether a stored frontmatter value holds anything (a blank string or an
    empty FAQ is nothing)."""
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def rejected_flag(field: str, value: Any, profile: BlogProfile, stored: Any) -> str:
    """The flag for a model value that failed the rules and was not written: what
    the model sent, and whether the stored value was kept or the field left empty
    (never "kept the stored one" when nothing was stored)."""
    tail = "kept the stored one" if _present(stored) else "left empty"
    if field == "summary":
        rule = profile.summary
        n = blog_rules.word_count(value)
        return (f"model's summary was {n} words "
                f"(needs {rule.min_words}-{rule.max_words}) — {tail}")
    if field == "faq":
        return (f"model's FAQ had {len(blog_rules.clean_faq(value))} item(s) "
                f"(needs {profile.faq.min}-{profile.faq.max}) — {tail}")
    return f'model\'s category "{value}" is not one of this blog\'s keys — {tail}'


def enabled_fields(profile: BlogProfile) -> set[str]:
    """The frontmatter fields this blog uses: always the category, plus the
    summary and FAQ unless the profile turns them off."""
    return {"category"} | {
        f for f in ("summary", "faq") if getattr(profile, f).enabled
    }


def failing_fields(article: dict, profile: BlogProfile) -> set[str]:
    """The enabled frontmatter fields whose stored value fails the rules (the only
    ones the frontmatter step regenerates)."""
    return {
        f for f in enabled_fields(profile) if not field_ok(f, article.get(f), profile)
    }


def meta_over_limits(article: dict, profile: BlogProfile) -> bool:
    """The title the site shows, the meta title or the description is too long."""
    tmax, dmax = profile.meta.title_max, profile.meta.description_max
    title = article.get("title") or ""
    mt = (article.get("meta_title") or "").strip()
    return (
        (len(title) > tmax and not (mt and len(mt) <= tmax))
        or len(mt) > tmax
        or len(article.get("meta_description") or "") > dmax
    )


def _humanize(raw: dict[str, Any]) -> dict[str, Any]:
    """Deterministic em-dash backstop on the model's summary and FAQ answers (the
    same one the reviser uses), applied before validation so the word-count and
    FAQ rules judge the text that is actually written. Leaves anything that isn't
    a string as-is for validate_frontmatter to reject."""
    out = dict(raw)
    if isinstance(out.get("summary"), str):
        out["summary"] = prose_style.thin_em_dashes(out["summary"])
    if isinstance(out.get("faq"), list):
        out["faq"] = [_humanize_item(item) for item in out["faq"]]
    return out


def _humanize_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    # Both answer keys clean_faq accepts.
    return {
        k: prose_style.thin_em_dashes(v) if k in ("a", "answer") and isinstance(v, str)
        else v
        for k, v in item.items()
    }


async def _attempt(
    client: PowabaseClient, msg: str, profile: BlogProfile,
    cluster_category: str | None, fields: set[str],
) -> tuple[dict[str, Any], list[str]]:
    raw = await _ask(client, msg)
    if raw is not None:
        raw = _humanize(raw)
    clean, flags = blog_rules.validate_frontmatter(
        raw or {}, profile, cluster_category=cluster_category
    )
    keys = {c.key for c in profile.categories}
    if cluster_category not in keys and (raw or {}).get("category") not in keys:
        # Neither the cluster nor the model named a real key: the fallback was used.
        flags.append(f"category defaulted to {clean['category']}")
    # These flags start with the field name; keep the requested ones.
    flags = [f for f in flags if f.split(" ", 1)[0] in fields]
    if raw is None:
        flags.insert(0, _UNPARSEABLE)
    return clean, flags


async def generate(
    client: PowabaseClient, db: Database, article_id: UUID, *,
    fields: set[str] | None = None,
    before_write: Callable[[], None] | None = None,
) -> list[str]:
    """Write category/summary/FAQ (only `fields`, default all enabled ones). Asks
    once, retries once on flags and keeps the attempt with fewer flags (the first on
    a tie). A field is written only when its new value passes the rules, so a bad or
    empty reply never replaces a stored value. `before_write` runs once, just before
    the write. Returns the chosen attempt's flags; [] when the brand has no profile
    (nothing written)."""
    article = gen_svc.get_article(db, article_id)
    if not article or not article.get("business_id"):
        return []
    brand = brands.get_profile(db, article["business_id"])
    profile = blog_rules.profile_of(brand)
    if not profile:
        return []
    if fields is None:
        fields = enabled_fields(profile)
    if not fields:
        return []
    name = (brand or {}).get("name") or "the brand"
    cc = _cluster_category(db, article)
    msg = _prompt(article, name, profile)
    clean, flags = await _attempt(client, msg, profile, cc, fields)
    if flags:
        retry = (
            f"{msg}\n\n## Your previous answer had these problems — fix them\n"
            + "\n".join(f"- {f}" for f in flags)
        )
        clean2, flags2 = await _attempt(client, retry, profile, cc, fields)
        if len(flags2) < len(flags):
            clean, flags = clean2, flags2
    write = {
        k: clean[k] for k in FM_FIELDS
        if k in fields and field_ok(k, clean.get(k), profile)
    }
    # A requested summary/FAQ that failed the rules isn't written: say so, and
    # whether the stored value stayed or the field is still empty.
    for k in ("summary", "faq"):
        if k in fields and k not in write and getattr(profile, k).enabled:
            flags = [f for f in flags if f.split(" ", 1)[0] != k]
            flags.append(rejected_flag(k, clean.get(k), profile, article.get(k)))
    stored = article.get("category")
    defaulted = f"category defaulted to {clean.get('category')}"
    if defaulted in flags and field_ok("category", stored, profile):
        # A forced run never swaps a valid stored category for the fallback.
        write.pop("category", None)
        flags = [
            f"category kept as {stored} (the model's was not a listed key)"
            if f == defaulted else f for f in flags
        ]
    # A value equal to the stored one is no change: no write and no version
    # (compared by value — dict equality ignores FAQ key order).
    write = {k: v for k, v in write.items() if v != article.get(k)}
    if write:
        if before_write:
            before_write()
        gen_svc._update(db, article_id, **write)
    return flags


def enforce_meta(
    db: Database, article_id: UUID, article: dict, profile: BlogProfile, *,
    before_write: Callable[[], None] | None = None,
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
        if before_write:
            before_write()
        gen_svc._update(db, article_id, **fields)


async def complete(
    client: PowabaseClient, db: Database, article_id: UUID, *,
    force: bool = False,
) -> list[str]:
    """Bring the frontmatter within the blog rules, touching only what fails them:
    meta (model, then clamp) only when the title/meta exceed the limits; category /
    summary / FAQ only for the fields that currently fail; and drop a body FAQ
    section (the FAQ lives in frontmatter). Used after generation and by "Fix
    automatically". `force` ("Generate summary & FAQ") also regenerates a passing
    summary and FAQ; the category is regenerated only when it fails (a valid
    stored category — perhaps picked by hand — is never replaced, forced or not).
    A new value is still written only when it passes the rules and differs from
    the stored one. The prior state is versioned once, just before the first write
    (no write, no version), so hand edits it overwrites can be reverted. Returns
    the frontmatter step's flags."""
    from . import brief as brief_svc
    from . import revise

    article = gen_svc.get_article(db, article_id)
    if not article or not article.get("business_id"):
        return []
    profile = blog_rules.profile_of(brands.get_profile(db, article["business_id"]))
    if not profile:
        return []
    failing = failing_fields(article, profile)
    if force:
        # Summary and FAQ only: the category is regenerated when it fails (above).
        failing |= enabled_fields(profile) - {"category"}
    fix_meta = meta_over_limits(article, profile)
    body_faq = profile.faq.enabled and bool(
        blog_rules.BODY_FAQ_RE.search(article.get("content_md") or "")
    )
    if not (failing or fix_meta or body_faq):
        return []

    snapped = False

    def snap() -> None:
        nonlocal snapped
        if not snapped:
            snapped = True
            gen_svc.snapshot_version(db, article)

    if fix_meta:
        brief = (
            brief_svc.get_brief(db, article["brief_id"])
            if article.get("brief_id") else {}
        ) or {}
        await revise.fix_meta(
            client, db, article_id, article, brief,
            title_max=profile.meta.title_max,
            description_max=profile.meta.description_max,
            before_write=snap,
        )
        fresh = gen_svc.get_article(db, article_id) or article
        enforce_meta(db, article_id, fresh, profile, before_write=snap)
    flags: list[str] = []
    if failing:
        flags = await generate(
            client, db, article_id, fields=failing, before_write=snap
        )
    if body_faq:
        md = (gen_svc.get_article(db, article_id) or {}).get("content_md") or ""
        if blog_rules.BODY_FAQ_RE.search(md):
            snap()
            gen_svc._update(db, article_id, content_md=blog_rules.strip_body_faq(md))
    return flags
