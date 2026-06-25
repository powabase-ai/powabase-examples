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
