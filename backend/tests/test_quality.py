"""Fact-check (reflect) — fallback path + kb context (hermetic)."""

from unittest.mock import AsyncMock, MagicMock

from rankforge_backend.services import quality


def test_kb_context_none_without_research_run():
    assert quality._kb_context(
        MagicMock(), {"research_run_id": None, "business_id": None}
    ) == (None, None)


async def test_reflect_uses_broad_path_when_no_kb(monkeypatch):
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
    client.run_agent = AsyncMock(
        return_value={
            "content": '{"grounding_score": 80, "claims_checked": 2, '
            '"supported": 2, "flagged": []}'
        }
    )

    rep = await quality.reflect(client, db, "a1")

    assert rep["grounding_score"] == 80
    # No KB → no per-claim extraction; exactly one (broad-bundle) judge call.
    assert client.run_agent.await_count == 1
    assert stored["grounding_report"]["grounding_score"] == 80
