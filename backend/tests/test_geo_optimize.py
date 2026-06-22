"""GEO optimize — deterministic JSON-LD builder (hermetic)."""

from rankforge_backend.services.geo_optimize import build_article_jsonld


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
