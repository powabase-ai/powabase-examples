"""GEO optimize — deterministic JSON-LD builder (hermetic)."""

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from rankforge_backend.models.blog import BlogProfile  # noqa: E402
from rankforge_backend.services import geo_optimize  # noqa: E402
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
    monkeypatch.setattr(
        geo_optimize, "ensure_faq_agent", AsyncMock(return_value="aid")
    )
    client = MagicMock()
    client.run_agent = AsyncMock(
        return_value={"content": '{"faqs": ["just a string", 123]}'}
    )
    assert await geo_optimize.build_faq_jsonld(client, "# Article") is None


_PROF = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}]})


def test_faq_jsonld_from_items():
    d = geo_optimize.faq_jsonld_from_items([{"q": "Q?", "a": "A."}, {"q": "", "a": "x"}])
    assert d["@type"] == "FAQPage" and len(d["mainEntity"]) == 1
    assert geo_optimize.faq_jsonld_from_items([]) is None


async def test_optimize_uses_stored_faq_with_profile():
    art = {
        "id": "a",
        "business_id": "b",
        "title": "T",
        "content_md": "# T\n\nx",
        "faq": [{"q": "Q?", "a": "A."}],
        "meta_description": "d",
    }
    with patch.object(geo_optimize.gen_svc, "get_article", return_value=art), patch.object(
        geo_optimize, "_brand", return_value={"name": "Brand", "blog_profile": _PROF.model_dump()}
    ), patch.object(
        geo_optimize, "build_faq_jsonld", AsyncMock()
    ) as extract, patch.object(
        geo_optimize.gen_svc, "_update"
    ) as upd:
        await geo_optimize.optimize_and_store(MagicMock(), MagicMock(), "a")
        extract.assert_not_called()
        graph = upd.call_args.kwargs["json_ld"]["@graph"]
        assert any(n.get("@type") == "FAQPage" for n in graph)


async def test_optimize_extracts_when_faq_disabled():
    off = _PROF.model_copy(update={"faq": _PROF.faq.model_copy(update={"enabled": False})})
    art = {
        "id": "a",
        "business_id": "b",
        "title": "T",
        "content_md": "# T\n\nx",
        "faq": None,
        "meta_description": "d",
    }
    with patch.object(geo_optimize.gen_svc, "get_article", return_value=art), patch.object(
        geo_optimize, "_brand", return_value={"name": "Brand", "blog_profile": off.model_dump()}
    ), patch.object(
        geo_optimize, "build_faq_jsonld", AsyncMock(return_value=None)
    ) as extract, patch.object(
        geo_optimize.gen_svc, "_update"
    ):
        await geo_optimize.optimize_and_store(MagicMock(), MagicMock(), "a")
        extract.assert_awaited_once()
