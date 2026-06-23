"""GEO optimize — deterministic JSON-LD builder (hermetic)."""

from unittest.mock import AsyncMock, MagicMock

from rankforge_backend.services import geo_optimize, scoring
from rankforge_backend.services.geo_optimize import (
    build_article_jsonld,
    build_howto_jsonld,
    build_itemlist_jsonld,
)


def test_itemlist_and_howto_from_h2s():
    md = "# Title\n\n## First item\nbody\n\n## Second item\nbody"
    il = build_itemlist_jsonld(md, "Title")
    assert il["@type"] == "ItemList"
    assert [i["name"] for i in il["itemListElement"]] == ["First item", "Second item"]
    assert il["itemListElement"][0]["position"] == 1

    ho = build_howto_jsonld(md, "Title")
    assert ho["@type"] == "HowTo"
    assert ho["step"][1]["name"] == "Second item"

    assert build_itemlist_jsonld("# No h2 here", "x") is None


def test_build_article_jsonld():
    ld = build_article_jsonld(
        {
            "title": "Headless CMS Guide",
            "meta_description": "A guide.",
            "content_md": "one two three four",
            "created_at": "2026-06-19T00:00:00Z",
            "updated_at": "2026-06-19T00:00:00Z",
        },
        {"primary_keyword": "headless cms", "secondary_keywords": ["graphql api"]},
        "Petal SEO",
    )
    assert ld["@type"] == "BlogPosting"
    assert ld["headline"] == "Headless CMS Guide"
    assert ld["keywords"] == ["headless cms", "graphql api"]
    assert ld["wordCount"] == 4
    assert ld["author"] == {"@type": "Organization", "name": "Petal SEO"}
    assert ld["datePublished"] == "2026-06-19T00:00:00Z"


async def test_build_faq_jsonld_survives_malformed_response(monkeypatch):
    # A FAQ response that's a list of non-dicts must not crash (AttributeError).
    monkeypatch.setattr(scoring, "ensure_judge_agent", AsyncMock(return_value="aid"))
    client = MagicMock()
    client.run_agent = AsyncMock(
        return_value={"content": '{"faqs": ["just a string", 123]}'}
    )
    assert await geo_optimize.build_faq_jsonld(client, "# Article") is None
