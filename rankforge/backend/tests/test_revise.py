"""Auto-revision evaluator gates (pure helpers) + editorial loop wiring."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from rankforge_backend.services import geo_optimize, quality, revise, scoring

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
# Axis meets target overall (88 >= 80) but hides one critically weak aspect (20).
SEO_MET_BUT_CRITICAL = {
    "total": 88, "target": 80, "met": True,
    "signals": [
        {"key": "heading_structure", "label": "Heading hierarchy", "score": 20,
         "fixes": ["Add more H2 sections."]},
        {"key": "keyword_density", "label": "Keyword usage", "score": 95, "fixes": []},
    ],
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


# --- critical sub-signal on an otherwise-met axis ---
def test_satisfied_false_when_met_axis_hides_critical_signal():
    # SEO meets target (88) but one aspect is critically low (20) → keep refining it.
    assert not revise.satisfied(SEO_MET_BUT_CRITICAL, GEO_MET, GR_OK)


def test_satisfied_true_when_only_critical_signal_is_meta_bound():
    # A critically low TITLE/meta signal is fix_meta's job, not the body loop's — it
    # must not wedge the objective loop into a pointless body rewrite it can't satisfy.
    seo = {
        "total": 88, "target": 80, "met": True,
        "signals": [{"key": "title_length", "label": "Title length", "score": 0,
                     "fixes": ["Target 30–60 characters."]}],
    }
    assert revise.satisfied(seo, GEO_MET, GR_OK)


def test_collect_issues_surfaces_critical_signal_on_met_axis():
    issues = revise.collect_issues(SEO_MET_BUT_CRITICAL, GEO_MET, None)
    assert any("Heading hierarchy" in i for i in issues)
    # The healthy 95-scoring signal on the same met axis is left alone (no flooding).
    assert not any("Keyword usage" in i for i in issues)


def test_collect_issues_names_critical_signal_even_without_canned_fix():
    seo = {
        "total": 88, "target": 80, "met": True,
        "signals": [{"key": "extractability", "label": "Extractable formatting",
                     "score": 15, "explanation": "Few lists/tables.", "fixes": []}],
    }
    issues = revise.collect_issues(seo, GEO_MET, None)
    assert any("Extractable formatting" in i and "15/100" in i for i in issues)


# --- revision commit gate ---
def _s(total, target):
    return {"total": total, "target": target}


def test_decide_accepts_seo_gain_while_geo_stays_above_target():
    # SEO 72→78 (gap 8→2); GEO 88→86 (both ≥85). Old raw-total guard would veto
    # this (160→164 is fine, but 72+90→78+86 = 162→164 ok; the real win is when GEO
    # drops): SEO 72→78, GEO 90→86 — raw total 162→164 up, but the key is targets.
    assert revise._decide([_s(72, 80), _s(90, 85)], [_s(78, 80), _s(86, 85)])


def test_decide_rejects_pushing_met_axis_below_target():
    # SEO improves but GEO falls from 90 to 84 (below its 85 target).
    assert not revise._decide([_s(72, 80), _s(90, 85)], [_s(78, 80), _s(84, 85)])


def test_decide_rejects_widening_the_gap():
    # SEO regresses 78→74 (gap 2→6); GEO unchanged & met.
    assert not revise._decide([_s(78, 80), _s(88, 85)], [_s(74, 80), _s(88, 85)])


def test_decide_allows_gap_neutral_edit():
    # Unchanged SEO/GEO — let it through; the outer combined-score check (grounding)
    # decides whether the pass actually helped.
    assert revise._decide([_s(73, 80), _s(88, 85)], [_s(73, 80), _s(88, 85)])


# --- objective loop: post-rescore commit decision (Stage 1) ---
def test_objective_total_sums_and_tolerates_missing_grounding():
    assert revise._objective_total(_s(80, 80), _s(85, 85), {"grounding_score": 70}) == 235
    # Grounding unavailable (None) or absent contributes 0 — must never crash.
    assert revise._objective_total(_s(80, 80), _s(85, 85), {"grounding_score": None}) == 165
    assert revise._objective_total(_s(80, 80), _s(85, 85), None) == 165


def test_met_regressed_badly():
    met = {"total": 88, "target": 85, "met": True}
    within_tol = {"total": 82, "target": 85}  # 3 below target — within tolerance
    wrecked = {"total": 80, "target": 85}      # 5 below target — collateral damage
    assert not revise._met_regressed_badly([(met, within_tol)])
    assert revise._met_regressed_badly([(met, wrecked)])
    # An axis that wasn't met to begin with can't "regress badly".
    unmet = {"total": 60, "target": 85, "met": False}
    assert not revise._met_regressed_badly([(unmet, wrecked)])


def _objective_env(monkeypatch, before, after, new_md="X" * 500):
    """Wire _objective_loop's collaborators against a mutable article state.
    reflect/score mutate the state to `after` so the loop's AFTER-rescore decision
    (the whole point of Stage 1) runs on realistic data."""
    state = {"art": {"content_md": "body", **before}}
    monkeypatch.setattr(revise.gen_svc, "get_article", lambda db, aid: dict(state["art"]))
    monkeypatch.setattr(
        revise.gen_svc, "_update", lambda db, aid, **f: state["art"].update(f)
    )
    monkeypatch.setattr(revise, "ensure_reviser_agent", AsyncMock(return_value="rv"))
    monkeypatch.setattr(revise, "_diverse_excerpts", AsyncMock(return_value="(none)"))
    monkeypatch.setattr(revise, "_revise_once", AsyncMock(return_value=new_md))

    async def _reflect(client, db, aid):
        state["art"]["grounding_report"] = after["grounding_report"]

    async def _score(client, db, aid):
        state["art"]["seo_score"] = after["seo_score"]
        state["art"]["geo_score"] = after["geo_score"]

    monkeypatch.setattr(quality, "reflect", _reflect)
    monkeypatch.setattr(geo_optimize, "optimize_and_store", AsyncMock())
    monkeypatch.setattr(scoring, "score_and_store", _score)
    return state


_GR_LOW_FLAGGED = {
    "grounding_score": 58,
    "flagged": [{"quote": "q", "issue": "i", "suggestion": "s"}],
}


async def test_objective_loop_keeps_a_grounding_gain(monkeypatch):
    """The user's bug: SEO/GEO are met (GEO only by a thin margin) but Grounding is
    low. The pass that lifts Grounding 58→73 must be KEPT, not vetoed."""
    before = {
        "seo_score": SEO_MET, "geo_score": {"total": 86, "target": 85, "met": True},
        "grounding_report": _GR_LOW_FLAGGED,
    }
    after = {
        "seo_score": SEO_MET, "geo_score": {"total": 86, "target": 85, "met": True},
        "grounding_report": {"grounding_score": 73, "flagged": []},
    }
    state = _objective_env(monkeypatch, before, after)
    await revise._objective_loop(MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {})
    assert state["art"]["content_md"] == "X" * 500  # rewrite kept
    assert state["art"]["grounding_report"]["grounding_score"] == 73


async def test_objective_loop_reverts_when_no_objective_gain(monkeypatch):
    """A pass that re-scores no better than before must revert content AND restore the
    cached scores (no half-applied state)."""
    before = {
        "seo_score": SEO_MET, "geo_score": GEO_MET,
        "grounding_report": _GR_LOW_FLAGGED,
    }
    after = {  # rescore shows nothing moved
        "seo_score": SEO_MET, "geo_score": GEO_MET,
        "grounding_report": _GR_LOW_FLAGGED,
    }
    state = _objective_env(monkeypatch, before, after)
    await revise._objective_loop(MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {})
    assert state["art"]["content_md"] == "body"  # reverted
    assert state["art"]["grounding_report"]["grounding_score"] == 58


async def test_objective_loop_reverts_when_met_axis_wrecked(monkeypatch):
    """Even a big Grounding gain is reverted if it wrecks an already-met axis past the
    tolerance — a higher combined total must not paper over real collateral damage."""
    before = {
        "seo_score": SEO_MET, "geo_score": {"total": 86, "target": 85, "met": True},
        "grounding_report": _GR_LOW_FLAGGED,
    }
    after = {  # grounding jumps to 85 but GEO collapses to 79 (target 85)
        "seo_score": SEO_MET, "geo_score": {"total": 79, "target": 85, "met": False},
        "grounding_report": {"grounding_score": 85, "flagged": []},
    }
    state = _objective_env(monkeypatch, before, after)
    await revise._objective_loop(MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {})
    assert state["art"]["content_md"] == "body"  # reverted despite higher total
    assert state["art"]["geo_score"]["total"] == 86


# --- editorial loop (LLM-editor-judged human-ness) ---
async def test_editor_review_parses_verdict():
    client = MagicMock()
    client.run_agent = AsyncMock(
        return_value={"content": '{"reads_human": 90, "verdict": "ship", "notes": []}'}
    )
    out = await revise._editor_review(client, "ed", "body", None)
    assert out["verdict"] == "ship"
    assert out["reads_human"] == 90


async def test_editor_review_fails_open_on_bad_output():
    """A flaky/unparseable judge must STOP editing (ship), never wedge the loop."""
    client = MagicMock()
    client.run_agent = AsyncMock(return_value={"content": "not json at all"})
    out = await revise._editor_review(client, "ed", "body", None)
    assert out["verdict"] == "ship"


async def test_editorial_loop_ships_without_rewrite(monkeypatch):
    """Editor verdict 'ship' → the reviser is never invoked; content untouched."""
    art = {"content_md": "Body.", "readability_score": None, "title": "T",
           "meta_title": None, "meta_description": None}
    monkeypatch.setattr(revise.gen_svc, "get_article", lambda db, aid: art)
    monkeypatch.setattr(revise, "ensure_editor_agent", AsyncMock(return_value="ed"))
    monkeypatch.setattr(
        revise, "_editor_review",
        AsyncMock(return_value={"verdict": "ship", "reads_human": 92, "notes": []}),
    )
    rev = AsyncMock()
    monkeypatch.setattr(revise, "_revise_for_voice", rev)
    await revise._editorial_loop(
        MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {}
    )
    rev.assert_not_awaited()


async def test_editorial_loop_skips_rewrite_when_no_notes(monkeypatch):
    """A 'revise' verdict with no actionable notes shouldn't spin a pointless rewrite."""
    art = {"content_md": "Body.", "readability_score": None, "title": "T",
           "meta_title": None, "meta_description": None}
    monkeypatch.setattr(revise.gen_svc, "get_article", lambda db, aid: art)
    monkeypatch.setattr(revise, "ensure_editor_agent", AsyncMock(return_value="ed"))
    monkeypatch.setattr(
        revise, "_editor_review",
        AsyncMock(return_value={"verdict": "revise", "reads_human": 60, "notes": []}),
    )
    rev = AsyncMock()
    monkeypatch.setattr(revise, "_revise_for_voice", rev)
    await revise._editorial_loop(
        MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {}
    )
    rev.assert_not_awaited()


async def test_editorial_loop_respects_revise_over_high_score(monkeypatch):
    """An explicit 'revise' must trigger a rewrite even if reads_human is high — a
    high score must not override a clear request to revise."""
    art = {"content_md": "Body here.", "readability_score": None, "title": "T",
           "meta_title": None, "meta_description": None}
    monkeypatch.setattr(revise.gen_svc, "get_article", lambda db, aid: art)
    monkeypatch.setattr(revise, "ensure_editor_agent", AsyncMock(return_value="ed"))
    monkeypatch.setattr(revise, "ensure_reviser_agent", AsyncMock(return_value="rv"))
    monkeypatch.setattr(
        revise, "_editor_review",
        AsyncMock(return_value={
            "verdict": "revise", "reads_human": 95,
            "notes": [{"quote": "x", "problem": "p", "fix": "f"}],
        }),
    )
    monkeypatch.setattr(revise, "_diverse_excerpts", AsyncMock(return_value="(none)"))
    rev = AsyncMock(return_value="")  # empty → loop breaks after, but it WAS attempted
    monkeypatch.setattr(revise, "_revise_for_voice", rev)
    await revise._editorial_loop(
        MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {}
    )
    rev.assert_awaited()


# --- user-directed targeted refine ---
ARTICLE_FOR_TARGETS = {
    "seo_score": {"signals": [
        {"key": "internal_links", "label": "Internal links", "score": 55,
         "fixes": ["Add internal links."]},
        {"key": "keyword_title", "label": "Title keyword", "score": 10,
         "fixes": ["Put the keyword in the title."]},  # meta-bound → fix_meta owns it
    ]},
    "geo_score": {"signals": []},
    "readability_score": {"signals": [
        {"key": "em_dashes", "label": "Em-dash restraint", "score": 30,
         "fixes": ["Thin out em-dashes; prefer commas, periods, or parentheses."]},
        {"key": "rhythm", "label": "Sentence-length variety", "score": 40, "fixes": []},
    ]},
    "grounding_report": {"grounding_score": 50, "flagged": [
        {"quote": "the flagged claim text", "issue": "unsupported",
         "suggestion": "cite a source"},
    ]},
}


def test_targeted_issues_selects_only_chosen_across_axes():
    issues = revise._targeted_issues(
        ARTICLE_FOR_TARGETS,
        ["readability:em_dashes", "seo:internal_links", "grounding:0"],
    )
    text = "\n".join(issues)
    assert "Em-dash restraint" in text and "Thin out em-dashes" in text
    assert "Internal links" in text
    assert "the flagged claim text" in text  # grounding claim picked by index
    assert "Sentence-length variety" not in text  # an unselected signal is excluded


def test_targeted_issues_skips_meta_bound_signals():
    # keyword_title is meta-bound — the body reviser can't fix it; fix_meta does.
    assert revise._targeted_issues(ARTICLE_FOR_TARGETS, ["seo:keyword_title"]) == []


def test_targeted_issues_falls_back_to_explanation_without_a_canned_fix():
    issues = revise._targeted_issues(
        {"readability_score": {"signals": [
            {"key": "rhythm", "label": "Rhythm", "score": 40, "fixes": [],
             "explanation": "Mechanically even."}]}},
        ["readability:rhythm"],
    )
    assert len(issues) == 1
    assert "Rhythm" in issues[0] and "Improve this aspect" in issues[0]


def test_selected_total_sums_selected_signals_plus_grounding():
    total = revise._selected_total(
        ARTICLE_FOR_TARGETS,
        ["readability:em_dashes", "seo:internal_links", "grounding:0"],
    )
    assert total == 30 + 55 + 50  # em_dashes + internal_links + grounding_score


async def test_refine_with_targets_runs_only_the_targeted_loop(monkeypatch):
    monkeypatch.setattr(
        revise.gen_svc, "get_article",
        lambda d, aid: {"id": aid, "brief_id": None, "seo_score": None},
    )
    monkeypatch.setattr(revise, "_article_context", lambda d, a: (None, {}, None))
    called = {"targeted": False, "objective": False, "editorial": False}

    async def _t(*a, **k):
        called["targeted"] = True

    async def _o(*a, **k):
        called["objective"] = True

    async def _e(*a, **k):
        called["editorial"] = True

    monkeypatch.setattr(revise, "_targeted_loop", _t)
    monkeypatch.setattr(revise, "_objective_loop", _o)
    monkeypatch.setattr(revise, "_editorial_loop", _e)
    await revise.refine(
        MagicMock(), MagicMock(), "aid", targets=["readability:em_dashes"]
    )
    assert called["targeted"]
    assert not called["objective"] and not called["editorial"]


async def test_refine_with_a_meta_target_runs_fix_meta(monkeypatch):
    monkeypatch.setattr(
        revise.gen_svc, "get_article",
        lambda d, aid: {"id": aid, "brief_id": None, "seo_score": {"signals": []}},
    )
    monkeypatch.setattr(revise, "_article_context", lambda d, a: (None, {}, None))
    monkeypatch.setattr(revise, "_targeted_loop", AsyncMock())
    fm = AsyncMock()
    monkeypatch.setattr(revise, "fix_meta", fm)
    monkeypatch.setattr(scoring, "score_and_store", AsyncMock())
    await revise.refine(
        MagicMock(), MagicMock(), "aid", targets=["seo:keyword_title"]
    )
    fm.assert_awaited_once()


def _targeted_env(monkeypatch, before, after, new_md="X" * 500):
    """Wire _targeted_loop's collaborators against a mutable article state, mirroring
    _objective_env. reflect/score mutate the state to `after` so the loop's post-rescore
    keep/revert decision runs on realistic data."""
    state = {"art": {"content_md": "body", **before}}
    monkeypatch.setattr(revise.gen_svc, "get_article", lambda db, aid: dict(state["art"]))
    monkeypatch.setattr(
        revise.gen_svc, "_update", lambda db, aid, **f: state["art"].update(f)
    )
    monkeypatch.setattr(revise, "ensure_reviser_agent", AsyncMock(return_value="rv"))
    monkeypatch.setattr(revise, "_diverse_excerpts", AsyncMock(return_value="(none)"))
    monkeypatch.setattr(revise, "_revise_once", AsyncMock(return_value=new_md))

    async def _reflect(client, db, aid):
        state["art"]["grounding_report"] = after["grounding_report"]

    async def _score(client, db, aid):
        state["art"]["seo_score"] = after["seo_score"]
        state["art"]["geo_score"] = after["geo_score"]
        state["art"]["readability_score"] = after.get("readability_score")

    monkeypatch.setattr(quality, "reflect", _reflect)
    monkeypatch.setattr(geo_optimize, "optimize_and_store", AsyncMock())
    monkeypatch.setattr(scoring, "score_and_store", _score)
    return state


# Selected signal: readability em_dashes. Before = 30; after the rewrite it does NOT
# improve (still 30), so _selected_total can't rise and the pass must be reverted.
_TARGETED_BEFORE = {
    "seo_score": SEO_MET, "geo_score": GEO_MET,
    "readability_score": {"signals": [
        {"key": "em_dashes", "label": "Em-dash restraint", "score": 30,
         "fixes": ["Thin out em-dashes."]},
    ]},
    "grounding_report": GR_OK,
}


async def test_targeted_loop_reverts_when_selected_total_unmoved(monkeypatch):
    """A targeted pass that doesn't raise the selected signals' combined score must
    restore BOTH the prior content and the cached scores (no half-applied state)."""
    after = {  # rescore shows the em-dash signal didn't move
        "seo_score": SEO_MET, "geo_score": GEO_MET,
        "readability_score": {"signals": [
            {"key": "em_dashes", "label": "Em-dash restraint", "score": 30,
             "fixes": ["Thin out em-dashes."]},
        ]},
        "grounding_report": GR_OK,
    }
    state = _targeted_env(monkeypatch, _TARGETED_BEFORE, after)
    reverts: list = []
    real_update = revise.gen_svc._update

    def _track(db, aid, **f):
        if f.get("content_md") == "body":
            reverts.append(f)
        real_update(db, aid, **f)

    monkeypatch.setattr(revise.gen_svc, "_update", _track)
    await revise._targeted_loop(
        MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {},
        ["readability:em_dashes"],
    )
    assert state["art"]["content_md"] == "body"  # content reverted
    # the revert call restored the snapshot (seo/geo/grounding/readability/json_ld)
    assert reverts and "seo_score" in reverts[-1] and "grounding_report" in reverts[-1]


async def test_targeted_loop_reverts_when_grounding_lost(monkeypatch):
    """Even when the SELECTED (readability) signal improves, a pass that quietly weakens
    factual grounding below target AND below prior must be reverted (the grounding
    guard) — a readability-only target can't be allowed to degrade grounding."""
    before = {**_TARGETED_BEFORE, "grounding_report": {"grounding_score": 80}}
    after = {  # em_dashes 30→90 (selected total rises) but grounding 80→50 collapses
        "seo_score": SEO_MET, "geo_score": GEO_MET,
        "readability_score": {"signals": [
            {"key": "em_dashes", "label": "Em-dash restraint", "score": 90, "fixes": []},
        ]},
        "grounding_report": {"grounding_score": 50},
    }
    state = _targeted_env(monkeypatch, before, after)
    await revise._targeted_loop(
        MagicMock(), MagicMock(), UUID(int=1), {}, None, None, {},
        ["readability:em_dashes"],
    )
    assert state["art"]["content_md"] == "body"  # reverted despite the selected gain
    assert state["art"]["grounding_report"]["grounding_score"] == 80
