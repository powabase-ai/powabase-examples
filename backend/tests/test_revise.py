"""Auto-revision evaluator gates (pure helpers)."""

from rankforge_backend.services import revise

SEO_MET = {"total": 82, "target": 80, "met": True, "signals": []}
SEO_FAIL = {
    "total": 60,
    "target": 80,
    "met": False,
    "signals": [
        {"label": "Heading hierarchy", "score": 50, "fixes": ["Add more H2 sections."]},
        {"label": "Readability", "score": 90, "fixes": ["Shorten sentences."]},
    ],
}
GEO_MET = {"total": 88, "target": 85, "met": True, "signals": []}
GR_OK = {"grounding_score": 80, "flagged": []}
GR_LOW = {
    "grounding_score": 40,
    "flagged": [{"claim": "c", "issue": "i", "suggestion": "s"}],
}


def test_satisfied_when_all_met():
    assert revise.satisfied(SEO_MET, GEO_MET, GR_OK)


def test_not_satisfied_when_seo_below_target():
    assert not revise.satisfied(SEO_FAIL, GEO_MET, GR_OK)


def test_not_satisfied_when_grounding_low():
    assert not revise.satisfied(SEO_MET, GEO_MET, GR_LOW)


def test_satisfied_when_grounding_unavailable():
    assert revise.satisfied(SEO_MET, GEO_MET, {"grounding_score": None})


def test_collect_issues_only_failing_below_floor():
    issues = revise.collect_issues(SEO_FAIL, GEO_MET, GR_LOW)
    assert any("Heading hierarchy" in i for i in issues)
    assert not any("Readability" in i for i in issues)  # scored above the floor
    assert any("Grounding" in i for i in issues)


def test_collect_issues_empty_when_met():
    assert revise.collect_issues(SEO_MET, GEO_MET, None) == []


def test_combined_score_sums_three_axes():
    assert revise.combined_score(SEO_MET, GEO_MET, GR_OK) == 82 + 88 + 80
    assert revise.combined_score(None, None, None) == 0
