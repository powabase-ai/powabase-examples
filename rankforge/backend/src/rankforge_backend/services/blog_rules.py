"""Pure blog-profile rules: word counts, clamps, URL shape, frontmatter and export
validation. No DB, no network, so every rule is unit-testable and shared by
generation, refine, linking and export."""

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from ..models.blog import BlogProfile

# An H2 whose text starts "FAQ"/"FAQs"/"Frequently asked…"/"Common questions".
BODY_FAQ_RE = re.compile(
    r"(?im)^##[ \t]+(?:faqs?\b|frequently[ \t]+asked|common[ \t]+questions)[^\n]*$"
)
_H2_RE = re.compile(r"(?m)^##[ \t]+")


def profile_of(brand: dict[str, Any] | None) -> BlogProfile | None:
    """The brand's parsed blog profile, or None (absent or unparseable → legacy)."""
    raw = (brand or {}).get("blog_profile")
    if not raw:
        return None
    try:
        return BlogProfile.model_validate(raw)
    except ValidationError:
        return None


def word_count(s: str | None) -> int:
    return len((s or "").split())


def trim_to_words(s: str, max_words: int) -> str:
    """At most `max_words` words, ending on the last sentence boundary that fits;
    a hard word cut when no boundary fits."""
    words = s.split()
    if len(words) <= max_words:
        return s
    cut = " ".join(words[:max_words])
    m = list(re.finditer(r"[.!?](?=\s|$)", cut))
    return cut[: m[-1].end()] if m else cut


def clamp_chars(s: str, limit: int) -> str:
    """At most `limit` code points, cut at a word boundary when possible."""
    s = s.strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if s[limit] == " ":  # the cut already ends on a word boundary
        return cut.rstrip(" ,;:-")
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;:-")


def with_trailing_slash(url: str) -> str:
    """Append '/' to the PATH (not the query/fragment); leave file-like paths alone."""
    p = urlsplit(url)
    path = p.path or "/"
    last = path.rsplit("/", 1)[-1]
    if not path.endswith("/") and "." not in last:
        path += "/"
    return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))


def hub_url(domain: str | None, path: str, trailing_slash: bool) -> str:
    base = (domain or "").strip().rstrip("/")
    if base and "//" not in base:
        base = f"https://{base}"
    url = f"{base}{path}" if base else path
    return with_trailing_slash(url) if trailing_slash else url


def fallback_category(profile: BlogProfile, cluster_category: str | None) -> str:
    keys = [c.key for c in profile.categories]
    if cluster_category in keys:
        return cluster_category  # type: ignore[return-value]
    tech = [c.key for c in profile.categories if c.technical]
    return (tech or keys)[0]


def _clean_faq(raw: Any) -> list[dict[str, str]]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or item.get("question") or "").strip()
        a = str(item.get("a") or item.get("answer") or "").strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out


def validate_frontmatter(
    raw: dict[str, Any], profile: BlogProfile, *, cluster_category: str | None
) -> tuple[dict[str, Any], list[str]]:
    """Coerce model output into valid frontmatter. Returns (clean, flags); a flag is
    a human-readable problem that still blocks export."""
    flags: list[str] = []
    keys = {c.key for c in profile.categories}
    if cluster_category in keys:
        category = cluster_category
    elif raw.get("category") in keys:
        category = raw["category"]
    else:
        category = fallback_category(profile, None)

    summary = None
    if profile.summary.enabled:
        summary = " ".join(str(raw.get("summary") or "").split())
        n = word_count(summary)
        if n > profile.summary.max_words:
            summary = trim_to_words(summary, profile.summary.max_words)
            flags.append("summary trimmed to fit; review it")
            n = word_count(summary)
        if n < profile.summary.min_words:
            flags.append(
                f"summary is {n} words (needs {profile.summary.min_words}-"
                f"{profile.summary.max_words})"
            )

    faq = None
    if profile.faq.enabled:
        faq = _clean_faq(raw.get("faq"))[: profile.faq.max]
        if len(faq) < profile.faq.min:
            flags.append(
                f"faq has {len(faq)} item(s) (needs {profile.faq.min}-{profile.faq.max})"
            )
    return {"category": category, "summary": summary, "faq": faq}, flags


def export_issues(article: dict[str, Any], profile: BlogProfile) -> list[str]:
    """Everything that would fail the target blog's build. Empty = exportable."""
    issues: list[str] = []
    keys = {c.key for c in profile.categories}
    cat = article.get("category")
    if not cat:
        issues.append("category is missing")
    elif cat not in keys:
        issues.append(f'unknown category "{cat}"')
    title = article.get("title") or ""
    shown = article.get("meta_title") if len(title) > profile.meta.title_max else None
    effective = shown or title
    if len(effective) > profile.meta.title_max:
        issues.append(
            f"title is {len(effective)} characters (max {profile.meta.title_max}); "
            "set a shorter meta title"
        )
    desc = article.get("meta_description") or ""
    if len(desc) > profile.meta.description_max:
        issues.append(
            f"description is {len(desc)} characters (max {profile.meta.description_max})"
        )
    if profile.summary.enabled:
        n = word_count(article.get("summary"))
        if not (profile.summary.min_words <= n <= profile.summary.max_words):
            issues.append(
                f"summary is {n} words (needs {profile.summary.min_words}-"
                f"{profile.summary.max_words})"
            )
    if profile.faq.enabled:
        n = len(_clean_faq(article.get("faq")))
        if not (profile.faq.min <= n <= profile.faq.max):
            issues.append(f"faq has {n} item(s) (needs {profile.faq.min}-{profile.faq.max})")
        if BODY_FAQ_RE.search(article.get("content_md") or ""):
            issues.append("remove the FAQ section in the body (the FAQ is frontmatter)")
    return issues


def strip_body_faq(md: str) -> str:
    """Drop an FAQ H2 section (heading through the line before the next H2)."""
    m = BODY_FAQ_RE.search(md)
    if not m:
        return md
    nxt = _H2_RE.search(md, m.end())
    end = nxt.start() if nxt else len(md)
    return (md[: m.start()].rstrip() + "\n\n" + md[end:].lstrip()).strip()
