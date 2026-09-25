"""Instruction-driven refine / rework (single pass, no score veto)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from rankforge_backend.models.article import RefineRequest
from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import revise

BODY = "# T\n\n" + "Paragraph with detail. " * 200
ART = {"id": "a", "business_id": "b", "brief_id": None, "research_run_id": None,
       "title": "T", "content_md": BODY, "meta_title": None, "meta_description": "d",
       "category": "rag", "summary": "s", "faq": None,
       "seo_score": {"total": 80}, "geo_score": {"total": 70},
       "readability_score": {"total": 75}}
PROF = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}]})


# --- request validation ---
def test_refine_rejects_blank_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(instructions="   ")


def test_refine_rejects_long_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(instructions="x" * 4001)


def test_refine_rejects_targets_with_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(targets=["seo:x"], instructions="do it")


def test_refine_rejects_mode_without_instructions():
    with pytest.raises(ValidationError):
        RefineRequest(mode="rework")


def test_refine_strips_instructions():
    assert RefineRequest(instructions="  tighten intro  ").instructions == "tighten intro"


# --- service ---
def _client(payload):
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={"content": json.dumps(payload)})
    return c


@pytest.fixture
def env():
    with patch.object(revise.gen_svc, "get_article", return_value=dict(ART)), \
         patch.object(revise.brands, "get_profile",
                      return_value={"name": "B", "blog_profile": PROF.model_dump(),
                                    "competitors": [{"domain": "rival.com"}]}), \
         patch.object(revise, "ensure_reviser_agent", AsyncMock(return_value="r")), \
         patch.object(revise, "_diverse_excerpts", AsyncMock(return_value="(none)")), \
         patch.object(revise.gen_svc, "snapshot_version") as snap, \
         patch.object(revise.gen_svc, "_update") as upd, \
         patch("rankforge_backend.services.quality.reflect", AsyncMock()), \
         patch("rankforge_backend.services.geo_optimize.optimize_and_store", AsyncMock()), \
         patch("rankforge_backend.services.scoring.score_and_store", AsyncMock()):
        yield snap, upd


async def test_refine_mode_writes_body_and_snapshots(env):
    snap, upd = env
    new = BODY.replace("Paragraph", "Para", 1) + " [r](https://rival.com/x)"
    await revise.instructed_pass(_client({"content_md": new}), MagicMock(), "a",
                                 instructions="tighten", mode="refine")
    snap.assert_called_once()
    body = next(c.kwargs["content_md"] for c in upd.call_args_list
                if "content_md" in c.kwargs)
    assert "rival.com" not in body  # competitor link stripped


async def test_refine_mode_rejects_short_body(env):
    _snap, upd = env
    with pytest.raises(revise.InstructedRefineError):
        await revise.instructed_pass(_client({"content_md": "# T\n\ntiny"}),
                                     MagicMock(), "a", instructions="x", mode="refine")
    assert not any("content_md" in c.kwargs for c in upd.call_args_list)


async def test_rework_mode_allows_short_body(env):
    _snap, upd = env
    await revise.instructed_pass(_client({"content_md": "# T\n\nNew angle. " * 5}),
                                 MagicMock(), "a", instructions="x", mode="rework")
    assert any("content_md" in c.kwargs for c in upd.call_args_list)


async def test_frontmatter_changes_validated(env):
    _snap, upd = env
    fm = {"summary": " ".join(["w"] * 90) + ".", "meta_title": "m" * 90,
          "faq": [{"q": "Q?", "a": "A"}] * 9}
    await revise.instructed_pass(_client({"content_md": BODY, "frontmatter": fm}),
                                 MagicMock(), "a", instructions="x", mode="refine")
    written = {k: v for c in upd.call_args_list for k, v in c.kwargs.items()}
    assert len(written["summary"].split()) <= 60
    assert len(written["faq"]) == 6
    prog = [c.kwargs["progress"] for c in upd.call_args_list
            if "progress" in c.kwargs]
    assert prog[-1]["frontmatter_flags"] == [
        "summary trimmed to fit; review it",
        "faq cut to 6 of the model's 9 items; review it",  # r3 minor
    ]


async def test_body_faq_removed_under_profile(env):
    _snap, upd = env
    body = BODY + "\n\n## FAQ\n\n### Q?\n\nA."
    await revise.instructed_pass(_client({"content_md": body}), MagicMock(), "a",
                                 instructions="x", mode="refine")
    written = next(c.kwargs["content_md"] for c in upd.call_args_list
                   if "content_md" in c.kwargs)
    assert "## FAQ" not in written


async def test_body_faq_kept_when_profile_faq_is_disabled(env):
    """Review r3 G8: with faq.enabled false the body FAQ is the blog's own FAQ."""
    _snap, upd = env
    off = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}],
                                      "faq": {"enabled": False}})
    body = BODY + "\n\n## FAQ\n\n### Q?\n\nA."
    with patch.object(revise.brands, "get_profile",
                      return_value={"name": "B", "blog_profile": off.model_dump()}):
        await revise.instructed_pass(_client({"content_md": body}), MagicMock(),
                                     "a", instructions="x", mode="refine")
    written = next(c.kwargs["content_md"] for c in upd.call_args_list
                   if "content_md" in c.kwargs)
    assert written == body


async def test_rollback_when_rescore_fails(env):
    _snap, upd = env
    with patch("rankforge_backend.services.quality.reflect",
               AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await revise.instructed_pass(_client({"content_md": BODY + " more"}),
                                         MagicMock(), "a", instructions="x", mode="refine")
    last = upd.call_args_list[-1].kwargs
    assert last["content_md"] == BODY  # restored


async def test_agent_failure_raises_without_write(env):
    _snap, upd = env
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={"error": "down"})
    with pytest.raises(revise.InstructedRefineError):
        await revise.instructed_pass(c, MagicMock(), "a", instructions="x", mode="refine")
    assert not any("content_md" in k.kwargs for k in upd.call_args_list)


# --- controller ruling R3: the reviser's system prompt asks for a full Markdown
# article, while this pass asks for JSON — it may reply with the article directly
# (ignoring the JSON ask). Fall back to using the whole reply as content_md instead of
# discarding a perfectly good revision.
async def test_instructed_pass_falls_back_to_bare_markdown_reply(env):
    _snap, upd = env
    reply = BODY.replace("Paragraph", "Para", 1)  # not JSON — a full article reply
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={"content": reply})
    await revise.instructed_pass(
        c, MagicMock(), "a", instructions="tighten", mode="refine"
    )
    body = next(k.kwargs["content_md"] for k in upd.call_args_list
                if "content_md" in k.kwargs)
    assert body == reply.strip()


async def test_instructed_pass_falls_back_through_markdown_fence(env):
    _snap, upd = env
    reply = BODY.replace("Paragraph", "Para", 1)
    fenced = f"```markdown\n{reply}\n```"
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={"content": fenced})
    await revise.instructed_pass(
        c, MagicMock(), "a", instructions="tighten", mode="refine"
    )
    body = next(k.kwargs["content_md"] for k in upd.call_args_list
                if "content_md" in k.kwargs)
    assert body == reply.strip()


async def test_instructed_pass_no_fallback_when_reply_is_not_an_article(env):
    """A reply that's neither valid JSON nor markdown starting with '#' (e.g. a
    refusal or stray prose) must NOT be treated as content_md — it should be an
    empty revision, not silently written."""
    _snap, upd = env
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={"content": "Sorry, I can't help."})
    # mode="rework" has no 60% length floor, so only the "not an article" check can
    # reject this reply (with "refine" the length check fired first).
    with pytest.raises(revise.InstructedRefineError, match="empty"):
        await revise.instructed_pass(
            c, MagicMock(), "a", instructions="x", mode="rework"
        )
    assert not any("content_md" in k.kwargs for k in upd.call_args_list)


# --- review r1 I6: a stream cut off before `complete` is not a successful rework ---
async def test_incomplete_stream_is_rejected(env):
    _snap, upd = env
    c = MagicMock()
    c.run_agent_collect = AsyncMock(return_value={
        "content": json.dumps({"content_md": "# T\n\nHalf an arti"}),
        "incomplete": True,
    })
    with pytest.raises(revise.InstructedRefineError, match="cut off"):
        await revise.instructed_pass(c, MagicMock(), "a", instructions="x",
                                     mode="rework")
    assert not any("content_md" in k.kwargs for k in upd.call_args_list)


# --- review r1 I5: empty model values never replace stored frontmatter ---
async def test_empty_frontmatter_values_keep_stored(env):
    _snap, upd = env
    fm = {"summary": "", "faq": [], "category": "  "}
    await revise.instructed_pass(_client({"content_md": BODY, "frontmatter": fm}),
                                 MagicMock(), "a", instructions="x", mode="refine")
    written = {k for c in upd.call_args_list for k in c.kwargs}
    assert not written & {"summary", "faq", "category"}


async def test_cluster_category_wins_over_model(env):
    prof = BlogProfile.model_validate({"categories": [
        {"key": "rag", "label": "R"}, {"key": "agents", "label": "A"}]})
    _snap, upd = env
    with patch.object(revise.gen_svc, "get_article",
                      return_value=dict(ART, cluster_id="c1")), \
         patch.object(revise.brands, "get_profile",
                      return_value={"name": "B", "blog_profile": prof.model_dump()}), \
         patch("rankforge_backend.services.clusters.get_cluster",
               return_value={"id": "c1", "category": "rag"}) as gc:
        await revise.instructed_pass(
            _client({"content_md": BODY, "frontmatter": {"category": "agents"}}),
            MagicMock(), "a", instructions="x", mode="refine")
    gc.assert_called_once()
    written = {k: v for c in upd.call_args_list for k, v in c.kwargs.items()}
    assert written["category"] == "rag"


# --- review r1 I4: instructed-pass frontmatter flags are kept, not dropped ---
async def test_frontmatter_flags_stored_in_progress(env):
    _snap, upd = env
    fm = {"summary": "too short"}
    await revise.instructed_pass(_client({"content_md": BODY, "frontmatter": fm}),
                                 MagicMock(), "a", instructions="x", mode="refine")
    prog = [c.kwargs["progress"] for c in upd.call_args_list
            if "progress" in c.kwargs]
    assert prog and prog[-1]["frontmatter_flags"] == [
        "model's summary was 2 words (needs 40-60) — kept the stored one"
    ]


# --- review r2 N5: an invalid incoming value never replaces the stored one ---
PROF2 = BlogProfile.model_validate({"categories": [
    {"key": "rag", "label": "R"}, {"key": "agents", "label": "A"}]})
S45 = " ".join(["word"] * 44) + " end."
FAQ4 = [{"q": f"Q{i}?", "a": "A."} for i in range(4)]
STORED = dict(ART, category="agents", summary=S45, faq=FAQ4)


async def _pass_with(fm_in):
    with patch.object(revise.gen_svc, "get_article", return_value=dict(STORED)), \
         patch.object(revise.brands, "get_profile",
                      return_value={"name": "B", "blog_profile": PROF2.model_dump()}), \
         patch.object(revise.gen_svc, "_update") as upd:
        await revise.instructed_pass(
            _client({"content_md": BODY, "frontmatter": fm_in}),
            MagicMock(), "a", instructions="x", mode="refine")
    written = {k: v for c in upd.call_args_list for k, v in c.kwargs.items()}
    prog = [c.kwargs["progress"] for c in upd.call_args_list if "progress" in c.kwargs]
    return written, (prog[-1].get("frontmatter_flags") if prog else [])


async def test_unknown_model_category_keeps_the_stored_one(env):
    written, flags = await _pass_with({"category": "AI agents"})
    assert "category" not in written  # not mapped to the fallback "rag"
    assert flags == ['model\'s category "AI agents" is not one of this blog\'s '
                     "keys — kept the stored one"]


async def test_short_model_summary_keeps_the_stored_one(env):
    written, flags = await _pass_with({"summary": "Too short here."})
    assert "summary" not in written
    assert flags == [
        "model's summary was 3 words (needs 40-60) — kept the stored one"
    ]


async def test_one_item_model_faq_keeps_the_stored_one(env):
    written, flags = await _pass_with({"faq": [{"q": "Only?", "a": "One."}]})
    assert "faq" not in written
    assert flags == [
        "model's FAQ had 1 item(s) (needs 3-6) — kept the stored one"
    ]


async def test_valid_model_frontmatter_is_written(env):
    new_s = " ".join(["fresh"] * 45)
    written, flags = await _pass_with(
        {"category": "rag", "summary": new_s, "faq": FAQ4[:3]})
    assert written["category"] == "rag" and written["summary"] == new_s
    assert written["faq"] == FAQ4[:3] and not flags


# --- review r2 G3: the refine floor is 60% of the current body ---
async def test_refine_mode_rejects_a_body_at_half_length(env):
    _snap, upd = env
    half = BODY[: len(BODY) // 2]
    with pytest.raises(revise.InstructedRefineError, match="60%"):
        await revise.instructed_pass(_client({"content_md": half}), MagicMock(),
                                     "a", instructions="x", mode="refine")
    assert not any("content_md" in c.kwargs for c in upd.call_args_list)


async def test_refine_mode_accepts_a_body_at_two_thirds(env):
    _snap, upd = env
    body = BODY[: len(BODY) * 2 // 3]
    await revise.instructed_pass(_client({"content_md": body}), MagicMock(),
                                 "a", instructions="x", mode="refine")
    assert any("content_md" in c.kwargs for c in upd.call_args_list)


# --- review r2 G14: instructed title / meta changes are written (stripped) ---
async def test_instructed_title_and_meta_are_written(env):
    _snap, upd = env
    fm_in = {"title": "  New title ", "meta_title": " New meta ",
             "meta_description": " New description. "}
    await revise.instructed_pass(
        _client({"content_md": BODY, "frontmatter": fm_in}), MagicMock(), "a",
        instructions="x", mode="refine")
    first = next(c.kwargs for c in upd.call_args_list if "content_md" in c.kwargs)
    assert first["title"] == "New title" and first["meta_title"] == "New meta"
    assert first["meta_description"] == "New description."


async def test_instructed_blank_title_and_meta_are_ignored(env):
    _snap, upd = env
    fm_in = {"title": "  ", "meta_title": "", "meta_description": 5}
    await revise.instructed_pass(
        _client({"content_md": BODY, "frontmatter": fm_in}), MagicMock(), "a",
        instructions="x", mode="refine")
    written = {k for c in upd.call_args_list for k in c.kwargs}
    assert not written & {"title", "meta_title", "meta_description"}


async def test_refine_mode_rejects_a_body_at_55_percent(env):
    _snap, upd = env
    body = BODY[: len(BODY) * 55 // 100]
    with pytest.raises(revise.InstructedRefineError, match="60%"):
        await revise.instructed_pass(_client({"content_md": body}), MagicMock(),
                                     "a", instructions="x", mode="refine")
    assert not any("content_md" in c.kwargs for c in upd.call_args_list)


# --- review r3 minor: "kept the stored one" only when something was stored ---
async def test_rejected_value_with_nothing_stored_says_left_empty(env):
    empty = dict(STORED, summary=None, faq=[], category=None)
    with patch.object(revise.gen_svc, "get_article", return_value=empty), \
         patch.object(revise.brands, "get_profile",
                      return_value={"name": "B", "blog_profile": PROF2.model_dump()}), \
         patch.object(revise.gen_svc, "_update") as upd:
        await revise.instructed_pass(
            _client({"content_md": BODY, "frontmatter": {
                "category": "AI agents", "summary": "Too short here.",
                "faq": [{"q": "Only?", "a": "One."}]}}),
            MagicMock(), "a", instructions="x", mode="refine")
    prog = [c.kwargs["progress"] for c in upd.call_args_list if "progress" in c.kwargs]
    assert prog[-1]["frontmatter_flags"] == [
        'model\'s category "AI agents" is not one of this blog\'s keys — left empty',
        "model's summary was 3 words (needs 40-60) — left empty",
        "model's FAQ had 1 item(s) (needs 3-6) — left empty",
    ]


async def test_faq_within_max_is_not_flagged_as_cut(env):
    written, flags = await _pass_with({"faq": FAQ4 + [{"q": "Blank?", "a": " "}]})
    assert written["faq"] == FAQ4 and flags == []  # a dropped blank item isn't a cut


# --- review r3 I5 / I9: a disabled summary/FAQ is never written by the pass ---
@pytest.mark.parametrize("off", ["summary", "faq"])
async def test_disabled_frontmatter_field_is_ignored(env, off):
    prof = BlogProfile.model_validate({
        "categories": [{"key": "rag", "label": "R"}], off: {"enabled": False}})
    new_s = " ".join(["fresh"] * 45)
    with patch.object(revise.gen_svc, "get_article", return_value=dict(STORED)), \
         patch.object(revise.brands, "get_profile",
                      return_value={"name": "B", "blog_profile": prof.model_dump()}), \
         patch.object(revise.gen_svc, "_update") as upd:
        await revise.instructed_pass(
            _client({"content_md": BODY,
                     "frontmatter": {"summary": new_s, "faq": FAQ4[:3]}}),
            MagicMock(), "a", instructions="x", mode="refine")
    written = {k: v for c in upd.call_args_list for k, v in c.kwargs.items()}
    on = ({"summary", "faq"} - {off}).pop()
    assert off not in written and on in written
    assert not [c for c in upd.call_args_list if "progress" in c.kwargs]  # no flags
