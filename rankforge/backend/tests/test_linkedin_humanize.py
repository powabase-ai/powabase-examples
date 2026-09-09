"""LinkedIn humanization (de-AI editorial loop) — the judge→revise loop, hermetic."""

from unittest.mock import AsyncMock

from rankforge_backend.services import linkedin_humanize as hz

_NOTE = [{"quote": "delve", "problem": "AI word", "fix": "cut it"}]


def _agents(monkeypatch):
    monkeypatch.setattr(hz, "ensure_editor_agent", AsyncMock(return_value="ed"))
    monkeypatch.setattr(hz, "ensure_reviser_agent", AsyncMock(return_value="rv"))


async def test_ships_clean_post_without_revising(monkeypatch):
    _agents(monkeypatch)
    monkeypatch.setattr(
        hz, "_judge_post",
        AsyncMock(return_value={"verdict": "ship", "reads_human": 92, "notes": []}),
    )
    revise = AsyncMock()
    monkeypatch.setattr(hz, "_revise_post", revise)

    out = await hz.humanize_post(AsyncMock(), "a clean, sharp post")

    assert out == "a clean, sharp post"
    revise.assert_not_awaited()  # already human → never rewrite


async def test_high_score_ships_even_without_explicit_ship(monkeypatch):
    _agents(monkeypatch)
    monkeypatch.setattr(
        hz, "_judge_post",
        AsyncMock(return_value={"verdict": "", "reads_human": 88, "notes": _NOTE}),
    )
    revise = AsyncMock()
    monkeypatch.setattr(hz, "_revise_post", revise)

    out = await hz.humanize_post(AsyncMock(), "a post")

    assert out == "a post"
    revise.assert_not_awaited()  # >=85 backstop when verdict is missing


async def test_revises_then_ships(monkeypatch):
    _agents(monkeypatch)
    monkeypatch.setattr(hz, "_judge_post", AsyncMock(side_effect=[
        {"verdict": "revise", "reads_human": 55, "notes": _NOTE},
        {"verdict": "ship", "reads_human": 90, "notes": []},
    ]))
    revise = AsyncMock(return_value="a humanized post that is plenty long enough")
    monkeypatch.setattr(hz, "_revise_post", revise)

    out = await hz.humanize_post(AsyncMock(), "delve into the robust tapestry of things")

    assert out == "a humanized post that is plenty long enough"
    assert revise.await_count == 1


async def test_caps_at_max_passes(monkeypatch):
    _agents(monkeypatch)
    # The judge is never satisfied — the loop must still terminate at MAX_PASSES.
    monkeypatch.setattr(
        hz, "_judge_post",
        AsyncMock(return_value={"verdict": "revise", "reads_human": 50, "notes": _NOTE}),
    )
    calls = {"n": 0}

    async def revise(*a, **k):
        calls["n"] += 1
        return f"revision number {calls['n']} with plenty of length to clear the guard"

    monkeypatch.setattr(hz, "_revise_post", revise)

    out = await hz.humanize_post(
        AsyncMock(), "an AI-slop post with plenty of length to clear the guard here"
    )

    assert calls["n"] == hz.MAX_PASSES
    assert out.startswith("revision number 2")


async def test_never_blanks_on_empty_revision(monkeypatch):
    _agents(monkeypatch)
    monkeypatch.setattr(
        hz, "_judge_post",
        AsyncMock(return_value={"verdict": "revise", "reads_human": 50, "notes": _NOTE}),
    )
    monkeypatch.setattr(hz, "_revise_post", AsyncMock(return_value="   "))  # blank rewrite

    original = "the original post body here that is reasonably long"
    out = await hz.humanize_post(AsyncMock(), original)

    assert out == original  # a blank revision is a failed rewrite — keep the post


async def test_rejects_gutted_revision(monkeypatch):
    _agents(monkeypatch)
    monkeypatch.setattr(
        hz, "_judge_post",
        AsyncMock(return_value={"verdict": "revise", "reads_human": 50, "notes": _NOTE}),
    )
    monkeypatch.setattr(hz, "_revise_post", AsyncMock(return_value="tiny"))  # <50% length

    original = "a much longer original post body that should survive a gutted rewrite"
    out = await hz.humanize_post(AsyncMock(), original)

    assert out == original


async def test_keeps_previous_when_revise_raises(monkeypatch):
    _agents(monkeypatch)
    monkeypatch.setattr(
        hz, "_judge_post",
        AsyncMock(return_value={"verdict": "revise", "reads_human": 50, "notes": _NOTE}),
    )
    monkeypatch.setattr(hz, "_revise_post", AsyncMock(side_effect=RuntimeError("upstream")))

    original = "the original post body that must survive a revise failure"
    out = await hz.humanize_post(AsyncMock(), original)

    assert out == original  # revise failure → keep original, never raise


async def test_editor_agent_failure_ships_original(monkeypatch):
    monkeypatch.setattr(
        hz, "ensure_editor_agent", AsyncMock(side_effect=RuntimeError("no agent"))
    )
    out = await hz.humanize_post(AsyncMock(), "some post body")
    assert out == "some post body"  # can't judge → ship what we have, never raise


async def test_empty_body_is_noop():
    assert await hz.humanize_post(AsyncMock(), "   ") == ""


async def test_strips_em_dashes_even_on_ship(monkeypatch):
    # The em-dash backstop is DETERMINISTIC — it fires even when the judge ships the post
    # (the LLM won't reliably remove its own em-dashes). This is the user-reported bug.
    _agents(monkeypatch)
    monkeypatch.setattr(
        hz, "_judge_post",
        AsyncMock(return_value={"verdict": "ship", "reads_human": 95, "notes": []}),
    )

    out = await hz.humanize_post(AsyncMock(), "We shipped it — fast — and it worked.")

    assert "—" not in out
    assert out == "We shipped it, fast, and it worked."


async def test_strips_em_dashes_the_reviser_reintroduces(monkeypatch):
    _agents(monkeypatch)
    monkeypatch.setattr(hz, "_judge_post", AsyncMock(side_effect=[
        {"verdict": "revise", "reads_human": 50, "notes": _NOTE},
        {"verdict": "ship", "reads_human": 90, "notes": []},
    ]))
    monkeypatch.setattr(
        hz, "_revise_post",
        AsyncMock(return_value="A revised post — with an em-dash the model re-added "
                              "— sadly, but long enough to pass the guard."),
    )

    out = await hz.humanize_post(AsyncMock(), "delve into the robust tapestry of things here")

    assert "—" not in out  # end-of-loop backstop drops what the reviser re-added


async def test_judge_post_swallows_client_error():
    client = AsyncMock()
    client.run_agent = AsyncMock(side_effect=RuntimeError("boom"))
    out = await hz._judge_post(client, "ed", "body")
    assert out == {"verdict": "ship", "reads_human": None, "notes": []}


async def test_judge_post_parses_fenced_json():
    client = AsyncMock()
    client.run_agent = AsyncMock(return_value={
        "content": '```json\n{"verdict":"revise","reads_human":40,"notes":[]}\n```'
    })
    out = await hz._judge_post(client, "ed", "body")
    assert out["verdict"] == "revise" and out["reads_human"] == 40


def test_score_coercion():
    # reads_human comes straight from model JSON; coerce robustly, reject junk and bool.
    assert hz._score(88) == 88
    assert hz._score(88.0) == 88
    assert hz._score("88") == 88
    assert hz._score(True) is None      # bool is an int subclass — must not read as 1
    assert hz._score(150) is None       # out of range
    assert hz._score("high") is None
    assert hz._score(None) is None


async def test_never_raises_on_malformed_verdict(monkeypatch):
    # A non-string verdict (straight from json.loads) must not crash the loop (blocker 2).
    _agents(monkeypatch)
    monkeypatch.setattr(hz, "_judge_post", AsyncMock(side_effect=[
        {"verdict": ["revise"], "reads_human": 40, "notes": _NOTE},
        {"verdict": "ship", "reads_human": 90, "notes": []},
    ]))
    revise = AsyncMock(return_value="a fine revised post that is plenty long enough here")
    monkeypatch.setattr(hz, "_revise_post", revise)

    out = await hz.humanize_post(AsyncMock(), "delve into the robust tapestry of things")

    assert isinstance(out, str)          # did not raise
    assert revise.await_count == 1       # low score → still revised, not wrongly shipped


async def test_keeps_previous_when_revision_drops_url(monkeypatch):
    # The 50% length guard can't catch a dropped ~25-char link; the structure check must.
    _agents(monkeypatch)
    monkeypatch.setattr(hz, "_judge_post", AsyncMock(side_effect=[
        {"verdict": "revise", "reads_human": 50, "notes": _NOTE},
        {"verdict": "ship", "reads_human": 90, "notes": []},
    ]))
    monkeypatch.setattr(
        hz, "_revise_post",
        AsyncMock(return_value="A slick rewrite that quietly dropped the link entirely, sadly."),
    )
    original = (
        "Great hook here.\n\nWhy it matters, in detail.\n\n"
        "Full write-up → https://blog.acme.com/p\n\n#AI #Dev"
    )

    out = await hz.humanize_post(AsyncMock(), original)

    assert "https://blog.acme.com/p" in out   # URL survived → the revision was rejected
    assert out == original


async def test_revise_post_rejects_truncated_output():
    client = AsyncMock()
    client.run_agent = AsyncMock(
        return_value={"content": "half a post", "stop_reason": "max_tokens"}
    )
    import pytest
    with pytest.raises(RuntimeError):
        await hz._revise_post(client, "rv", "body", [{"quote": "x", "problem": "y", "fix": "z"}])


async def test_revise_post_strips_preamble():
    client = AsyncMock()
    client.run_agent = AsyncMock(
        return_value={"content": "Here's your revised post:\n\nThe real first line."}
    )
    out = await hz._revise_post(client, "rv", "body", [])
    assert out == "The real first line."   # the load-bearing hook, not the chatter
