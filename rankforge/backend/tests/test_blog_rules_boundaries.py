"""Off-by-one boundaries of export_issues / validate_frontmatter (PR #26 review
round 1: every boundary mutation survived before these)."""

import pytest

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import blog_rules as br

P = BlogProfile.model_validate({
    "categories": [{"key": "rag", "label": "R"}],
    "summary": {"min_words": 5, "max_words": 7},
    "faq": {"min": 3, "max": 6},
    "meta": {"title_max": 60, "description_max": 160},
})


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _faq(n: int) -> list[dict[str, str]]:
    return [{"q": f"Q{i}?", "a": "A."} for i in range(n)]


def _art(**over):
    return {"category": "rag", "title": "T" * 40, "meta_title": "",
            "meta_description": "d" * 100, "summary": _words(6), "faq": _faq(4),
            "content_md": "Body.", **over}


def _issues(**over) -> list[str]:
    return br.export_issues(_art(**over), P)


def test_baseline_article_is_clean():
    assert _issues() == []


@pytest.mark.parametrize(("n", "ok"), [(4, False), (5, True), (7, True), (8, False)])
def test_export_summary_word_bounds(n, ok):
    assert (not any("summary" in i for i in _issues(summary=_words(n)))) is ok


@pytest.mark.parametrize(("n", "ok"), [(2, False), (3, True), (6, True), (7, False)])
def test_export_faq_count_bounds(n, ok):
    assert (not any("faq" in i for i in _issues(faq=_faq(n)))) is ok


@pytest.mark.parametrize(("n", "ok"), [(60, True), (61, False)])
def test_export_title_bound_without_meta_title(n, ok):
    assert (not any("title" in i for i in _issues(title="T" * n))) is ok


@pytest.mark.parametrize(("n", "ok"), [(60, True), (61, False)])
def test_export_long_title_uses_meta_title_bound(n, ok):
    out = _issues(title="T" * 80, meta_title="M" * n)
    assert (not any("title" in i for i in out)) is ok


@pytest.mark.parametrize(("n", "ok"), [(160, True), (161, False)])
def test_export_description_bound(n, ok):
    out = _issues(meta_description="d" * n)
    assert (not any("description" in i for i in out)) is ok


@pytest.mark.parametrize(("n", "flagged", "kept"), [
    (4, True, 4), (5, False, 5), (7, False, 7), (8, True, 7),
])
def test_validate_summary_word_bounds(n, flagged, kept):
    clean, flags = br.validate_frontmatter(
        {"summary": _words(n), "faq": _faq(3)}, P, cluster_category=None
    )
    assert any("summary" in f for f in flags) is flagged
    assert br.word_count(clean["summary"]) == kept


@pytest.mark.parametrize(("n", "flagged", "kept"), [
    (2, True, 2), (3, False, 3), (6, False, 6), (7, False, 6),
])
def test_validate_faq_count_bounds(n, flagged, kept):
    clean, flags = br.validate_frontmatter(
        {"summary": _words(6), "faq": _faq(n)}, P, cluster_category=None
    )
    assert any("faq" in f for f in flags) is flagged
    assert len(clean["faq"]) == kept
