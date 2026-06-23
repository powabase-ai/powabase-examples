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


def test_collect_issues_excludes_meta_bound_signals():
    # Title/meta-bound signals are handled by fix_meta, not the body reviser.
    seo = {
        "met": False,
        "signals": [
            {"key": "title_length", "label": "Title length", "score": 0,
             "fixes": ["Target 30–60 characters."]},
            {"key": "heading_structure", "label": "Heading hierarchy", "score": 40,
             "fixes": ["Add more H2 sections."]},
        ],
    }
    issues = revise.collect_issues(seo, None, None)
    assert any("Heading hierarchy" in i for i in issues)
    assert not any("Title length" in i for i in issues)


def test_combined_score_sums_three_axes():
    assert revise.combined_score(SEO_MET, GEO_MET, GR_OK) == 82 + 88 + 80
    assert revise.combined_score(None, None, None) == 0


# --- revision commit gate ---
def _s(total, target):
    return {"total": total, "target": target}


def test_decide_accepts_seo_gain_while_geo_stays_above_target():
    # SEO 72→78 (gap 8→2); GEO 88→86 (both ≥85). Old raw-total guard would veto
    # this (160→164 is fine, but 72+90→78+86 = 162→164 ok; the real win is when GEO
    # drops): SEO 72→78, GEO 90→86 — raw total 162→164 up, but the key is targets.
    assert revise._decide(_s(72, 80), _s(90, 85), _s(78, 80), _s(86, 85))


def test_decide_rejects_pushing_met_axis_below_target():
    # SEO improves but GEO falls from 90 to 84 (below its 85 target).
    assert not revise._decide(_s(72, 80), _s(90, 85), _s(78, 80), _s(84, 85))


def test_decide_rejects_widening_the_gap():
    # SEO regresses 78→74 (gap 2→6); GEO unchanged & met.
    assert not revise._decide(_s(78, 80), _s(88, 85), _s(74, 80), _s(88, 85))


def test_decide_allows_gap_neutral_edit():
    # Unchanged SEO/GEO — let it through; the outer combined-score check (grounding)
    # decides whether the pass actually helped.
    assert revise._decide(_s(73, 80), _s(88, 85), _s(73, 80), _s(88, 85))
