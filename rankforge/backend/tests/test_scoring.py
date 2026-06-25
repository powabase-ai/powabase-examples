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
    # LLM signals are still present (neutral 50) so the weight denominator — and
    # thus the target's meaning — is the same whether or not the judge ran.
    assert by["direct_answer"]["score"] == 50


def test_geo_includes_llm_signals_when_present():
    g = scoring.score_geo(MD, BRIEF, {"direct_answer": 90, "citability": 80})
    by = {x["key"]: x for x in g["signals"]}
    assert by["direct_answer"]["score"] == 90
    assert by["direct_answer"]["method"] == "llm"


def test_keyword_density_ignores_substrings():
    # "cms" appears only inside "cmsx" — word-boundary counting must score it 0.
    body = "# Title\n" + "cmsx cmsx cmsx alpha beta gamma delta epsilon zeta eta " * 3
    s = scoring.score_seo(body, "Title", "x" * 140, {"primary_keyword": "cms"})
    density = next(x for x in s["signals"] if x["key"] == "keyword_density")
    assert "0.00%" in density["explanation"]


def test_keyword_density_matches_punctuation_keyword():
    # ".net" must match via non-word lookarounds (\b would score it 0).
    body = "# Title\n" + ".net .net .net " + "word " * 20
    s = scoring.score_seo(body, "Title", "x" * 140, {"primary_keyword": ".net"})
    density = next(x for x in s["signals"] if x["key"] == "keyword_density")
    assert "0.00%" not in density["explanation"]


def test_band_and_helpers():
    assert scoring._band(1.0, 0.5, 1.5, 1.0) == 100
    assert scoring._band(2.5, 0.5, 1.5, 1.0) == 0
    assert scoring._flesch("This is easy to read. Short words help.") > 0


def test_readability_flags_ai_tells():
    aiish = (
        "In today's fast-paced world, we delve into the vibrant landscape. "
        "It's not just powerful, it's seamless and robust. Let's dive in. "
        "Moreover, this is pivotal. Furthermore, it is crucial. In conclusion, "
        "we unlock and elevate and harness and leverage the realm."
    )
    s = scoring.score_readability(aiish, None)
    assert {"total", "target", "met", "signals"} <= set(s)
    by = {x["key"]: x["score"] for x in s["signals"]}
    assert by["ai_vocabulary"] < 50  # stacked delve/leverage/robust/… register
    assert by["tell_phrases"] < 50  # "in today's…", "it's not just", "let's dive in"
    assert not s["met"]
    # LLM signals default to neutral 50 when the judge is absent.
    assert by["human_voice"] == 50


def test_readability_clean_prose_scores_well():
    clean = (
        "The API returns 200 in about 40ms. We measured it across 1,000 requests "
        "on a t3.micro. Sarah on the platform team flagged a regression last March; "
        "the fix shipped in v2.3. Short sentence. Then a longer one that explains "
        "the trade-off between cache size and cold-start latency in concrete terms."
    )
    by = {x["key"]: x["score"] for x in scoring.score_readability(clean, None)["signals"]}
    assert by["ai_vocabulary"] == 100
    assert by["tell_phrases"] == 100


def test_readability_uses_llm_human_voice_when_present():
    s = scoring.score_readability("Some prose.", {"human_voice": 90, "flow": 80})
    by = {x["key"]: x for x in s["signals"]}
    assert by["human_voice"]["score"] == 90
    assert by["human_voice"]["method"] == "llm"


def test_readability_gate_fails_on_em_dash_spam():
    """An egregious hard tell (em-dash spam) must flip an otherwise-passing article
    to not-met, so refine's collect_issues (which skips met axes) surfaces it."""
    base = (
        "The API returns 200 in about 40 ms. We checked 1,000 requests on a "
        "t3.micro. Sarah flagged a regression in March. The fix shipped in v2.3 "
        "and nothing regressed since, which surprised the on-call engineer."
    )
    llm = {"human_voice": 95, "flow": 95}
    assert scoring.score_readability(base, llm)["met"]  # clean prose passes
    emdash = base.replace(". ", " — ", 5)  # inject 5 em-dashes into the same prose
    s = scoring.score_readability(emdash, llm)
    by = {x["key"]: x["score"] for x in s["signals"]}
    assert by["em_dashes"] < 40
    assert not s["met"]  # gated to not-met despite a high weighted average
    assert s["total"] == scoring.READABILITY_TARGET - 1
