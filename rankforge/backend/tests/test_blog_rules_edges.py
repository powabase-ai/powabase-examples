"""Edge cases of the PR #26 blog_rules helpers that mutation testing found
unpinned (review round 2: B11 and the other blog_rules survivors)."""

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import blog_rules as br

P = BlogProfile.model_validate({
    "categories": [{"key": "rag", "label": "R"}],
    "summary": {"min_words": 5, "max_words": 8},
    "faq": {"min": 1, "max": 6},
    "meta": {"title_max": 60, "description_max": 160},
})


# --- profile parsing ---
def test_empty_stored_profile_counts_as_absent_not_invalid():
    assert br.profile_of({"blog_profile": {}}) is None
    assert br.invalid_profile_reason({"blog_profile": {}}) is None


def test_invalid_reason_names_the_field_and_the_pydantic_message():
    raw = {**P.model_dump(), "links": {**P.model_dump()["links"], "min": -1}}
    reason = br.invalid_profile_reason({"blog_profile": raw})
    assert reason is not None and reason.startswith("links.min: ")
    assert reason != "links.min: invalid"  # pydantic's own message, not a stub


# --- trim_to_words / clamp_chars ---
def test_trim_to_words_leaves_exactly_max_words_untouched():
    assert br.trim_to_words("One two. Three four", 4) == "One two. Three four"


def test_trim_to_words_cuts_back_to_the_last_boundary():
    assert br.trim_to_words("One two. Three four five", 4) == "One two."


def test_clamp_chars_strips_before_measuring():
    assert br.clamp_chars("   padded   ", 6) == "padded"


def test_clamp_chars_hard_cuts_when_the_only_space_is_too_early():
    assert br.clamp_chars("a bcdefghijklmnop", 10) == "a bcdefghi"


def test_clamp_chars_drops_trailing_punctuation_after_a_word_cut():  # B11
    assert br.clamp_chars("Hello, world again", 9) == "Hello"
    assert br.clamp_chars("Hello world, again", 12) == "Hello world"


# --- clean_faq ---
def test_clean_faq_accepts_question_answer_aliases_and_strips():
    raw = [{"question": "  Why?  ", "answer": " Because. "}]
    assert br.clean_faq(raw) == [{"q": "Why?", "a": "Because."}]


def test_clean_faq_drops_items_missing_either_half_and_non_dicts():
    raw = [{"q": "Only a question?", "a": "  "}, {"q": "", "a": "Only answer."},
           "not a dict", None, {"q": "Kept?", "a": "Yes."}]
    assert br.clean_faq(raw) == [{"q": "Kept?", "a": "Yes."}]
    assert br.clean_faq({"q": "x", "a": "y"}) == []  # not a list


# --- validate_frontmatter / export_issues ---
def test_trimmed_summary_below_the_minimum_is_flagged_short():
    # 12 words; the only sentence boundary that fits under 8 words leaves 3.
    raw = {"category": "rag", "summary": "One two three. " + " ".join(["w"] * 9),
           "faq": [{"q": "Q?", "a": "A."}]}
    clean, flags = br.validate_frontmatter(raw, P, cluster_category=None)
    assert clean["summary"] == "One two three."
    assert "summary trimmed to fit; review it" in flags
    assert "summary is 3 words (needs 5-8)" in flags


def test_long_meta_title_is_ignored_while_the_title_fits():
    art = {"category": "rag", "title": "T" * 50, "meta_title": "M" * 80,
           "meta_description": "d", "summary": "a b c d e f", "faq":
           [{"q": "Q?", "a": "A."}], "content_md": "Body."}
    assert br.export_issues(art, P) == []


def test_common_questions_heading_is_a_body_faq():
    md = "Intro.\n\n## Common questions\n\nQ and A.\n\n## Next\n\nMore."
    assert br.BODY_FAQ_RE.search(md)
    assert br.strip_body_faq(md) == "Intro.\n\n## Next\n\nMore."
