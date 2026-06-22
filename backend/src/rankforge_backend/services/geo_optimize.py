"""GEO optimize — emit schema.org JSON-LD (BlogPosting + FAQPage) so answer engines
can parse the article. BlogPosting is built deterministically; the FAQ is extracted
by the JSON judge agent. Stored on articles.json_ld and rendered in the page.
"""

from typing import Any
from uuid import UUID

from ..db import Database
from ..powabase import PowabaseClient
from ..util import extract_json
from . import brief as brief_svc
from . import business_profiles as brands
from . import generation as gen_svc
from . import scoring


def _iso(v: Any) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)


def build_article_jsonld(
    article: dict[str, Any], brief: dict[str, Any], author_name: str | None
) -> dict[str, Any]:
    keywords = []
    if brief.get("primary_keyword"):
        keywords.append(brief["primary_keyword"])
    keywords += brief.get("secondary_keywords") or []
    ld: dict[str, Any] = {
        "@type": "BlogPosting",
        "headline": (article.get("title") or "")[:110],
        "description": article.get("meta_description") or "",
        "keywords": keywords,
        "wordCount": len((article.get("content_md") or "").split()),
    }
    if _iso(article.get("created_at")):
        ld["datePublished"] = _iso(article["created_at"])
    if _iso(article.get("updated_at")):
        ld["dateModified"] = _iso(article["updated_at"])
    if author_name:
        ld["author"] = {"@type": "Organization", "name": author_name}
    return ld


_FAQ_PROMPT = (
    "Extract the FAQ from this article. Return ONLY "
    '{"faqs": [{"question": str, "answer": str}]} — concise answers (<=60 words), '
    "only questions the article actually answers, max 8. If none, return "
    '{"faqs": []}.'
)


async def build_faq_jsonld(
    client: PowabaseClient, content_md: str
) -> dict[str, Any] | None:
    try:
        agent_id = await scoring.ensure_judge_agent(client)
        res = await client.run_agent(
            agent_id, f"{_FAQ_PROMPT}\n\n---ARTICLE---\n{content_md[:16000]}"
        )
        faqs = (extract_json(res.get("content") or "")).get("faqs") or []
    except Exception:  # noqa: BLE001
        return None
    entities = [
        {
            "@type": "Question",
            "name": f["question"],
            "acceptedAnswer": {"@type": "Answer", "text": f["answer"]},
        }
        for f in faqs
        if f.get("question") and f.get("answer")
    ]
    if not entities:
        return None
    return {"@type": "FAQPage", "mainEntity": entities}


async def optimize_and_store(
    client: PowabaseClient, db: Database, article_id: UUID
) -> dict[str, Any] | None:
    article = gen_svc.get_article(db, article_id)
    if article is None:
        return None
    brief = (
        brief_svc.get_brief(db, article["brief_id"]) if article.get("brief_id") else {}
    ) or {}
    brand = (
        brands.get_profile(db, article["business_id"])
        if article.get("business_id")
        else None
    )
    author = brand["name"] if brand else None

    graph = [build_article_jsonld(article, brief, author)]
    faq = await build_faq_jsonld(client, article.get("content_md") or "")
    if faq:
        graph.append(faq)
    json_ld = {"@context": "https://schema.org", "@graph": graph}
    gen_svc._update(db, article_id, json_ld=json_ld)
    return json_ld
