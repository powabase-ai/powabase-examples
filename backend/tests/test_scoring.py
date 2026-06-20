"""SEO/GEO deterministic scorers (pure, hermetic)."""

from rankforge_backend.services import scoring

BRIEF = {
    "primary_keyword": "headless cms",
    "secondary_keywords": ["content modeling", "graphql api"],
    "entities": ["Strapi", "Contentful"],
    "questions": ["What is a headless CMS?"],
    "target_word_count": 60,
}
MD = """# The Best Headless CMS Guide

A headless CMS like Strapi separates content from presentation. [Strapi](https://strapi.io)
offers a GraphQL API and flexible content modeling for modern teams.

## What is a headless CMS?

A headless CMS is a back-end content repository. Contentful and Strapi are popular options.

- Decoupled architecture
- API-first delivery
"""
META = "A practical guide to the headless cms for content modeling and graphql api delivery."


def test_seo_shape_and_keyword_title():
    s = scoring.score_seo(MD, "The Best Headless CMS Guide", META, BRIEF)
    assert {"total", "target", "met", "signals"} <= set(s)
    by = {x["key"]: x for x in s["signals"]}
    assert by["keyword_title"]["score"] == 100  # "headless cms" in the title
    assert 0 <= s["total"] <= 100


def test_geo_deterministic_signals():
    g = scoring.score_geo(MD, BRIEF, None)
    by = {x["key"]: x for x in g["signals"]}
    assert by["entity_coverage"]["score"] == 100  # Strapi + Contentful present
    assert by["structured_data"]["score"] == 0  # no JSON-LD
    assert by["citation_density"]["score"] > 0  # one outbound citation
    assert "direct_answer" not in by  # llm signals omitted when llm is None


def test_geo_includes_llm_signals_when_present():
    g = scoring.score_geo(MD, BRIEF, {"direct_answer": 90, "citability": 80})
    by = {x["key"]: x for x in g["signals"]}
    assert by["direct_answer"]["score"] == 90
    assert by["direct_answer"]["method"] == "llm"


def test_band_and_helpers():
    assert scoring._band(1.0, 0.5, 1.5, 1.0) == 100
    assert scoring._band(2.5, 0.5, 1.5, 1.0) == 0
    assert scoring._flesch("This is easy to read. Short words help.") > 0
