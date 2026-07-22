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


def test_seo_signal_weights_sum_to_one():
    # The aggregate target only means what it says if the weights sum to 1 — guard
    # against a rebalance silently shifting the denominator.
    s = scoring.score_seo(MD, "The Best Headless CMS Guide", META, BRIEF)
    assert round(sum(sig["weight"] for sig in s["signals"]), 6) == 1.0


def test_readability_signal_weights_sum_to_one():
    # Same invariant for readability — a new signal (e.g. brand_voice) must shave a
    # sibling, not push the denominator past 1.0 and silently dilute every other signal.
    s = scoring.score_readability(MD, {"human_voice": 80, "flow": 80})
    assert round(sum(sig["weight"] for sig in s["signals"]), 6) == 1.0


def test_low_keyword_density_scores_low_and_names_the_keyword():
    # A primary keyword that appears ~once in a long article is genuinely under-used;
    # the recalibrated band must score that low (the old 1.5 slack scored it ~77).
    body = "# Title\nheadless cms " + "filler word here ok " * 250
    s = scoring.score_seo(body, "Title", "x" * 140, {"primary_keyword": "headless cms"})
    kd = next(x for x in s["signals"] if x["key"] == "keyword_density")
    assert kd["score"] < 50
    assert kd["fixes"] and "headless cms" in kd["fixes"][0]  # actionable, not generic


def test_secondary_coverage_fix_lists_the_missing_keywords():
    brief = {"primary_keyword": "x", "secondary_keywords": ["alpha term", "beta term"]}
    body = "# Title\nThis mentions alpha term once. " + "filler " * 50
    s = scoring.score_seo(body, "Title", "x" * 140, brief)
    sc = next(x for x in s["signals"] if x["key"] == "secondary_coverage")
    assert sc["fixes"] and "beta term" in sc["fixes"][0]  # the missing one is named
    assert "alpha term" not in sc["fixes"][0]  # already covered → not asked for again


def test_competitor_links_flags_rival_and_names_it():
    # A dofollow link to a configured competitor domain scores the signal down and the
    # fix names the exact domain to unlink; the brand's own + neutral links are ignored.
    body = (
        "# Guide\n\nWe compare options. See [Rival](https://www.rival.com/pricing) "
        "and [our docs](https://mybrand.com/docs) and [a study](https://research.org/x)."
    )
    s = scoring.score_seo(
        body, "Guide", "x" * 140, {"primary_keyword": "guide"},
        competitor_hosts={"rival.com"},
    )
    cl = next(x for x in s["signals"] if x["key"] == "competitor_links")
    assert cl["score"] == 50  # one competitor link → 100 - 1*50
    assert cl["fixes"] and "rival.com" in cl["fixes"][0]


def test_competitor_links_matches_subdomains():
    body = "# G\n\n[Rival blog](https://blog.rival.com/post)."
    s = scoring.score_seo(
        body, "G", "x" * 140, {"primary_keyword": "g"}, competitor_hosts={"rival.com"}
    )
    cl = next(x for x in s["signals"] if x["key"] == "competitor_links")
    assert cl["score"] == 50  # blog.rival.com matches the rival.com competitor


def test_competitor_links_clean_when_no_hosts():
    # No competitors configured (or none passed) → the guardrail is a clean 100 no-op.
    body = "# Guide\n\n[Rival](https://rival.com/x) is one option."
    s = scoring.score_seo(body, "Guide", "x" * 140, {"primary_keyword": "guide"})
    cl = next(x for x in s["signals"] if x["key"] == "competitor_links")
    assert cl["score"] == 100 and cl["fixes"] == []


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


def test_readability_flags_antithesis_reframe():
    # "X isn't A. It's B" / "isn't about A, it's about B" — the negate-then-reveal tic.
    md = (
        "The way forward isn't more tools. It's better process. "
        "Success isn't about speed, it's about consistency."
    )
    by = {x["key"]: x["score"] for x in scoring.score_readability(md, None)["signals"]}
    assert by["tell_phrases"] < 100  # the reframe construction was detected


def test_readability_flags_genuinely_intensifier():
    # "genuinely" as an intensifier is an AI-register tell → dings ai_vocabulary.
    md = "This is genuinely useful, genuinely matters, and genuinely helps you ship."
    by = {x["key"]: x["score"] for x in scoring.score_readability(md, None)["signals"]}
    assert by["ai_vocabulary"] < 100


def test_antithesis_detector_ignores_plain_negation():
    # A negation that is NOT the reframe (no "it's" payoff) must not trip the detector.
    clean = "The build is not green. We rolled back the change and paged the on-call."
    assert scoring._TELL_RE.search(clean) is None


def test_antithesis_detector_ignores_possessive_its():
    # Possessive "its" after a negation must NOT trip the reframe detector (it requires
    # the contraction apostrophe in the payoff, not the possessive).
    assert scoring._TELL_RE.search(
        "The API isn't deprecated, but its replacement is faster."
    ) is None
    assert scoring._TELL_RE.search(
        "The library wasn't slow, and its footprint stayed small."
    ) is None
    # The genuine contraction "it's" still trips it.
    assert scoring._TELL_RE.search(
        "The API isn't deprecated, it's just renamed."
    ) is not None


def test_readability_flags_detached_brand_voice():
    # The brand's own blog referring to itself in the third person / hedging its own
    # docs — the two narrowed, low-false-positive tells that survive.
    md = (
        "Powabase isolates every project. "
        "The vendor asserts that each runtime enforces hard, non-negotiable step "
        "limits across the whole execution path, even under sustained concurrent load. "
        "It works. "
        "According to Powabase's own internal docs, the safeguards always hold. "
        "The vendor claims strong, sensible defaults. "
        "Setup takes minutes, not days, and those defaults suit most teams out of "
        "the box. "
        "Try it."
    )
    sigs = {s["key"]: s for s in scoring.score_readability(md, None)["signals"]}
    assert sigs["brand_voice"]["score"] < 40  # 3 detached refs → dinged hard
    assert sigs["brand_voice"]["fixes"]  # actionable fix offered
    # ADVISORY, not a gate: even a badly-dinged brand_voice must NOT force the axis to
    # miss target when the prose is otherwise strong (this drove needless refines).
    res = scoring.score_readability(md, {"human_voice": 95, "flow": 95})
    assert res["total"] >= scoring.READABILITY_TARGET
    assert res["met"] is True


def test_brand_voice_ignores_competitor_comparisons():
    # Third-person attribution + hedging is EXACTLY what the writer prompt wants for
    # competitor comparisons — the narrowed regex must not false-positive on it.
    comp = (
        "Acme claims to be the fastest option on the market. "
        "Supabase allegedly caps row size. The platform states its pricing publicly, "
        "and the company asserts 99.9% uptime across all regions."
    )
    sigs = {s["key"]: s for s in scoring.score_readability(comp, None)["signals"]}
    assert sigs["brand_voice"]["score"] == 100  # no false positive on rival claims


def test_detached_voice_ignores_first_person_and_neutral_verbs():
    # First-person ("our own") and neutral verbs ("supports") must NOT trip it.
    clean = (
        "According to our own benchmarks, Powabase isolates each project. "
        "Our runtime enforces step limits. The platform supports batching."
    )
    assert scoring._DETACHED_VOICE_RE.search(clean) is None


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


def test_tell_detector_still_catches_every_original_construction():
    """Refactor guard: sourcing the regex from prose_style must not drop a pattern."""
    originals = [
        "it's not just a database, it's a platform",
        "the way forward isn't more tools. It's better process",
        "whether you're a beginner or a pro",
        "in today's fast-paced world",
        "let's dive in",
        "buckle up",
        "in conclusion",
        "at the end of the day",
    ]
    for phrase in originals:
        assert scoring._TELL_RE.search(phrase), phrase


def test_possessive_its_is_not_an_antithesis_reframe():
    """The load-bearing false-positive guard: possessive "its" must not trip it."""
    assert not scoring._TELL_RE.search(
        "The service isn't down, but its replacement is still warming up."
    )


def test_ai_word_detector_still_catches_the_original_register():
    for word in ("delve", "leveraging", "seamlessly", "nestled", "genuinely"):
        assert scoring._AI_WORD_RE.search(f"we {word} here"), word


def test_new_register_words_are_detected():
    for word in (
        "utilize", "facilitates", "empowering", "streamlined", "multifaceted",
        "meticulously", "intricate", "paramount", "transformative", "supercharge",
        "beacon", "cutting-edge", "ever-evolving", "paradigm shift", "game changer",
    ):
        assert scoring._AI_WORD_RE.search(f"a {word} thing"), word


def test_new_constructions_are_detected():
    for phrase in (
        "studies show that teams ship faster",
        "experts agree this is the way",
        "it plays a vital role in the pipeline",
        "what most people get wrong is the eval",
        "here's the thing, nobody measures it",
        "what if I told you the index was stale",
        "the launch adds search, highlighting the team's focus",
        "Not a framework. Not a library. A runtime.",
        "That's it. That's the whole migration.",
        "to sum up, the cache was cold",
        "the gateway serves as a central hub",
    ):
        assert scoring._TELL_RE.search(phrase), phrase


def test_empty_phrases_hype_and_recap_openers_are_detected():
    for phrase in (
        "it's worth noting that the cache is cold",
        "it is important to note the tradeoff",
        "at its core, the runtime is a queue",
        "the truth is nobody measured it",
        "in this article we cover indexing",
        "when it comes to latency, p99 matters",
        "going forward we will pin the version",
        "this is huge for the team",
        "this changes everything about deploys",
    ):
        assert scoring._TELL_RE.search(phrase), phrase


def test_recap_opener_needs_line_start_and_a_comma():
    """Line-anchored so ordinary sentences survive: "Overall performance improved" is
    a fact, "Overall, ..." is a recap. Requires the detector to compile with re.M."""
    assert scoring._TELL_RE.search("Ultimately, the migration paid off.")
    assert scoring._TELL_RE.search("## Section\nOverall, we cut latency in half.")
    assert not scoring._TELL_RE.search("Overall performance improved by 12%.")
    assert not scoring._TELL_RE.search("We ultimately, and reluctantly, rolled back.")


def test_precision_guards_hold_for_the_new_constructions():
    """High-precision only: ordinary technical prose must not trip the new patterns."""
    for clean in (
        # "acts as a" was deliberately excluded — it's normal technical writing
        "The proxy acts as a load balancer for the cluster.",
        # a named, linked source is exactly what we want, not weasel attribution
        "The 2025 Stack Overflow survey reports a 12% drop.",
        # a Markdown label colon is not a colon reveal
        "- **Latency**: 40ms at p99.",
        # "summary" as a noun, not a recap opener
        "The summary field accepts 160 characters.",
        # "the source of truth is" must not trip the "the truth is" filler
        "The single source of truth is the ledger table.",
        # "in order to" / "in terms of" were deliberately left out of empty_phrase
        "We shard the table in order to keep writes under 5ms.",
    ):
        assert not scoring._TELL_RE.search(clean), clean


def test_fake_profound_kicker_is_judge_only():
    """The closing-metaphor pattern is recognizable only in context, so it must reach
    the judge but never the deterministic detector."""
    from rankforge_backend.services import prose_style as ps

    kicker = next(p for p in ps.PATTERNS if p.key == "fake_profound_kicker")
    assert kicker.regex is None
    assert kicker.name in ps.judge_taxonomy()
    assert not scoring._TELL_RE.search(
        "The best systems are the ones you never think about."
    )


def test_every_contraction_matches_both_apostrophe_forms():
    """Rendered Markdown and LLM output emit the typographic apostrophe (U+2019) far
    more than the straight one, so a straight-only branch misses most real prose."""
    straight = [
        "it isn't just a database, it's a platform",
        "whether you're a beginner or a pro",
        "in today's fast-paced world",
        "let's dive in",
        "it's worth noting the cache is cold",
        "here's the thing, nobody measures it",
        "That's it. That's the whole migration.",
    ]
    for phrase in straight:
        assert scoring._TELL_RE.search(phrase), f"straight: {phrase}"
        curly = phrase.replace("'", "’")
        assert scoring._TELL_RE.search(curly), f"typographic: {curly}"
