"""Fact-check (reflect) — fallback path + kb context (hermetic)."""

from unittest.mock import AsyncMock, MagicMock

from rankforge_backend.services import quality


def test_kb_context_none_without_research_run():
    assert quality._kb_context(
        MagicMock(), {"research_run_id": None, "business_id": None}
    ) == (None, None)


async def test_reflect_unavailable_when_no_kb(monkeypatch):
    # No KB → grounding is unmeasurable; don't fabricate a score by judging against
    # nothing. Report unavailable (None) without calling the LLM at all.
    db = MagicMock()
    article = {
        "id": "a1",
        "brief_id": None,
        "business_id": None,
        "research_run_id": None,
        "content_md": "# A\nA specific claim.",
    }
    stored: dict = {}
    monkeypatch.setattr(quality.gen_svc, "get_article", lambda db, aid: article)
    monkeypatch.setattr(
        quality.gen_svc, "_update", lambda db, aid, **k: stored.update(k)
    )
    monkeypatch.setattr(quality.brief_svc, "get_brief", lambda db, bid: {})
    monkeypatch.setattr(quality, "ensure_factcheck_agent", AsyncMock(return_value="aid"))
    client = MagicMock()
    client.run_agent = AsyncMock()

    rep = await quality.reflect(client, db, "a1")

    assert rep["grounding_score"] is None and rep["error"]
    assert client.run_agent.await_count == 0  # never invoked the judge
    assert stored["grounding_report"]["error"]
