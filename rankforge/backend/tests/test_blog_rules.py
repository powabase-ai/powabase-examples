"""blog_rules — pure rule helpers."""

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import blog_rules as br

P = BlogProfile.model_validate({
    "categories": [
        {"key": "enterprise", "label": "E", "technical": False},
        {"key": "rag", "label": "R", "technical": True},
    ],
})
S45 = " ".join(["word"] * 44) + " end."


def test_profile_of_none_and_valid():
    assert br.profile_of(None) is None
    assert br.profile_of({"blog_profile": None}) is None
    assert br.profile_of({"blog_profile": P.model_dump()}).faq.max == 6


def test_profile_of_tolerates_corrupt_json():
    assert br.profile_of({"blog_profile": {"categories": "nope"}}) is None


def test_trim_to_words_prefers_sentence_boundary():
    s = "One two three. Four five six seven. Eight nine."
    assert br.trim_to_words(s, 7) == "One two three. Four five six seven."
    assert br.trim_to_words(s, 2) == "One two"  # no boundary fits → hard cut
    assert br.trim_to_words(s, 50) == s


def test_clamp_chars_word_boundary_and_codepoints():
    assert br.clamp_chars("hello brave new world", 15) == "hello brave new"
    assert br.clamp_chars("short", 60) == "short"
    assert len(br.clamp_chars("é" * 70, 60)) == 60


def test_with_trailing_slash_edge_cases():
    assert br.with_trailing_slash("https://x.ai/blog/a") == "https://x.ai/blog/a/"
    assert br.with_trailing_slash("https://x.ai/blog/a/") == "https://x.ai/blog/a/"
    assert br.with_trailing_slash("/free-mvp?ref=x") == "/free-mvp/?ref=x"
    assert br.with_trailing_slash("/free-mvp/#top") == "/free-mvp/#top"
    assert br.with_trailing_slash("/llms.txt") == "/llms.txt"
    assert br.with_trailing_slash("https://x.ai") == "https://x.ai/"


def test_hub_url():
    assert br.hub_url("powabase.ai", "/vector-database", True) == (
        "https://powabase.ai/vector-database/"
    )
    assert br.hub_url("https://powabase.ai/", "/x/", False) == "https://powabase.ai/x/"
    assert br.hub_url(None, "/x/", True) == "/x/"


def test_fallback_category_order():
    assert br.fallback_category(P, "enterprise") == "enterprise"
    assert br.fallback_category(P, "bogus") == "rag"  # first technical
    assert br.fallback_category(P, None) == "rag"


def test_validate_frontmatter_happy():
    faq = [{"q": f"Q{i}?", "a": "A."} for i in range(4)]
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": faq}, P, cluster_category=None
    )
    assert clean["category"] == "rag" and clean["summary"] == S45
    assert len(clean["faq"]) == 4 and flags == []


def test_validate_frontmatter_cluster_category_wins():
    clean, _ = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": []}, P, cluster_category="enterprise"
    )
    assert clean["category"] == "enterprise"


def test_validate_frontmatter_unknown_category_falls_back():
    clean, flags = br.validate_frontmatter(
        {"category": "nope", "summary": S45, "faq": []}, P, cluster_category=None
    )
    assert clean["category"] == "rag"
    assert not any("category" in f for f in flags)


def test_validate_frontmatter_summary_trimmed_and_flagged_short():
    long = " ".join(["w"] * 80) + "."
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": long, "faq": []}, P, cluster_category=None
    )
    assert br.word_count(clean["summary"]) <= 60
    assert any("summary trimmed" in f for f in flags)
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": "Too short.", "faq": []}, P, cluster_category=None
    )
    assert any("summary is 2 words" in f for f in flags)


def test_validate_frontmatter_faq_truncate_drop_empty_and_flag():
    faq = [{"q": f"Q{i}?", "a": "A."} for i in range(9)] + [{"q": "", "a": "x"}]
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": faq}, P, cluster_category=None
    )
    assert len(clean["faq"]) == 6
    clean, flags = br.validate_frontmatter(
        {"category": "rag", "summary": S45, "faq": [{"q": "Q?", "a": "A"}]},
        P, cluster_category=None,
    )
    assert any("faq has 1" in f for f in flags)


def test_validate_frontmatter_respects_disabled_rules():
    off = P.model_copy(update={
        "summary": P.summary.model_copy(update={"enabled": False}),
        "faq": P.faq.model_copy(update={"enabled": False}),
    })
    clean, flags = br.validate_frontmatter({"category": "rag"}, off, cluster_category=None)
    assert clean["summary"] is None and clean["faq"] is None and flags == []


def _art(**over):
    faq = [{"q": f"Q{i}?", "a": "A."} for i in range(3)]
    return {
        "title": "Short title", "meta_title": None, "meta_description": "d" * 100,
        "category": "rag", "summary": S45, "faq": faq,
        "content_md": "# T\n\n## Intro\n\nBody.", **over,
    }


def test_export_issues_clean():
    assert br.export_issues(_art(), P) == []


def test_export_issues_each_rule():
    issues = br.export_issues(_art(category=None), P)
    assert any("category" in i for i in issues)
    issues = br.export_issues(_art(category="nope"), P)
    assert any("unknown category" in i for i in issues)
    issues = br.export_issues(_art(title="x" * 70), P)
    assert any("title is 70" in i for i in issues)
    # A long H1 is fine when metaTitle fits.
    assert br.export_issues(_art(title="x" * 70, meta_title="short"), P) == []
    issues = br.export_issues(_art(meta_description="d" * 161), P)
    assert any("description is 161" in i for i in issues)
    issues = br.export_issues(_art(summary=None), P)
    assert any("summary" in i for i in issues)
    issues = br.export_issues(_art(faq=[]), P)
    assert any("faq" in i for i in issues)
    issues = br.export_issues(
        _art(content_md="# T\n\n## Frequently asked questions\n\n### Q?\n\nA."), P
    )
    assert any("FAQ section in the body" in i for i in issues)


def test_strip_body_faq_removes_section_until_next_h2():
    md = "# T\n\n## A\n\ntext\n\n## FAQ\n\n### Q?\n\nA.\n\n## Conclusion\n\nend"
    out = br.strip_body_faq(md)
    assert "## FAQ" not in out and "### Q?" not in out
    assert "## A" in out and "## Conclusion" in out
    assert br.strip_body_faq("# T\n\n## FAQs\n\n### Q\n\nA") == "# T"


def test_export_issues_strips_whitespace_meta_title():
    # render_markdown strips meta_title and emits no metaTitle for "   ", so the
    # 70-char title is what the site checks: the gate must see the same.
    issues = br.export_issues(_art(title="x" * 70, meta_title="   "), P)
    assert any("title is 70" in i for i in issues)


def test_export_issues_ignores_blank_faq_items():
    faq = [{"q": "Q?", "a": "A."}] * 2 + [{"q": "  ", "a": "A."}]
    issues = br.export_issues(_art(faq=faq), P)
    assert any("faq has 2" in i for i in issues)


def test_clean_faq_is_public_and_drops_blank_items():
    raw = [{"q": " Q? ", "a": " A. "}, {"q": " ", "a": "x"}, "junk"]
    assert br.clean_faq(raw) == [{"q": "Q?", "a": "A."}]


# --- review r3 survivors B20 / B2 ---
def test_an_h3_faqs_subsection_is_not_a_body_faq():
    """Only an H2 is the FAQ section: an H3 "FAQs" subsection inside a normal
    section is content, kept as written (it would otherwise be stripped up to the
    next H2)."""
    md = ("# T\n\n## Setup\n\ntext\n\n### FAQs about setup\n\nQ and A.\n\n"
          "More setup text.\n\n## Next\n\nend")
    assert br.BODY_FAQ_RE.search(md) is None
    assert br.strip_body_faq(md) == md


def test_an_h3_inside_the_faq_section_does_not_end_it():
    md = "# T\n\n## FAQ\n\n### Q one?\n\nA.\n\n### Q two?\n\nB.\n\n## End\n\nbye"
    assert br.strip_body_faq(md) == "# T\n\n## End\n\nbye"


def test_trim_to_words_never_cuts_inside_a_dotted_word():
    """'Node.js' holds a '.', but no sentence ends there: the boundary needs
    whitespace or the end of the text after the punctuation."""
    s = "Intro sentence here. We use Node.js for the backend today"
    assert br.trim_to_words(s, 7) == "Intro sentence here."
    assert br.trim_to_words("See e.g. Node.js docs. Then more words", 4) == (
        "See e.g. Node.js docs."
    )
