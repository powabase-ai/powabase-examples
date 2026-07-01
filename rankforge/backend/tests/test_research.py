"""Research — JSON extraction (unit) + async route wiring (hermetic)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import research as svc
from rankforge_backend.util import extract_json

BID = "11111111-1111-1111-1111-111111111111"
ROW = {
    "id": "33333333-3333-3333-3333-333333333333",
    "business_id": BID,
    "topic": "generative engine optimization",
    "locale": "en-US",
    "status": "searching",
    "error": None,
    "progress": {},
    "serp": {"results": [], "paa": [], "related_queries": []},
    "competitors": [],
    "clusters": [],
    "intent": None,
    "agent_run_id": None,
    "created_by": None,
    "created_at": "2026-06-18T00:00:00Z",
}


def test_extract_json_fenced():
    assert extract_json('x\n```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_bare():
    assert extract_json('note {"a": 2} end') == {"a": 2}


def test_extract_json_missing_raises():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_extract_json_fenced_keeps_nested_braces():
    # The fenced capture must be greedy or it truncates at the first "}".
    assert extract_json('```json\n{"a": {"b": 1}, "c": [1, 2]}\n```') == {
        "a": {"b": 1},
        "c": [1, 2],
    }


def test_diverse_urls_prefers_distinct_domains():
    urls = [
        "https://a.com/1",
        "https://www.a.com/2",
        "https://b.com/x",
        "https://c.com/y",
    ]
    out = svc.diverse_urls(urls, 3)
    assert [svc._domain(u) for u in out] == ["a.com", "b.com", "c.com"]


def test_diverse_urls_backfills_when_too_few_domains():
    urls = ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
    assert len(svc.diverse_urls(urls, 2)) == 2


def test_diverse_urls_skips_junk_domains():
    urls = [
        "https://youtube.com/watch?v=1",
        "https://reddit.com/r/x",
        "https://realsite.com/guide",
        "https://docs.example.com/api",
    ]
    out = svc.diverse_urls(urls, 4)
    domains = [svc._domain(u) for u in out]
    assert "youtube.com" not in domains and "reddit.com" not in domains
    assert "realsite.com" in domains and "docs.example.com" in domains


def test_is_usable_source():
    assert svc.is_usable_source({"status": "extracted", "word_count": 800})
    # failed/thin pages are not citable
    assert not svc.is_usable_source({"status": "failed", "word_count": 800})
    assert not svc.is_usable_source({"status": "extracted", "word_count": 30})
    assert not svc.is_usable_source({"status": "extracted", "word_count": None})


# --- source-quality scoring + prune/backfill ---
def _teardown(title: str, wc: int | None):
    from rankforge_backend.models.research import CompetitorTeardown

    return CompetitorTeardown(
        url=None, title=title, word_count=wc, headings=[], source_id=None
    )


async def test_score_sources_maps_scores(monkeypatch):
    monkeypatch.setattr(svc, "ensure_source_judge_agent", AsyncMock(return_value="ag"))
    client = MagicMock()
    client.run_agent_collect = AsyncMock(return_value={
        "error": None,
        "content": '```json\n[{"index":0,"score":90,"reason":"official docs"},'
                   '{"index":1,"score":20,"reason":"thin seo blog"}]\n```',
    })
    out = await svc.score_sources(client, [
        {"url": "https://docs.example.com/a", "title": "Docs", "word_count": 900},
        {"url": "https://spam.blog/x", "title": "Spam", "word_count": 1200},
    ])
    assert out["https://docs.example.com/a"] == (90, "official docs")
    assert out["https://spam.blog/x"] == (20, "thin seo blog")


async def test_score_sources_degrades_to_empty_on_error(monkeypatch):
    # Agent failure must NOT prune anything — return {} so every source is kept.
    monkeypatch.setattr(svc, "ensure_source_judge_agent", AsyncMock(return_value="ag"))
    client = MagicMock()
    client.run_agent_collect = AsyncMock(return_value={"error": "boom", "content": ""})
    out = await svc.score_sources(
        client, [{"url": "https://x.com", "title": "X", "word_count": 500}]
    )
    assert out == {}


async def test_score_sources_clamps_and_skips_bad(monkeypatch):
    monkeypatch.setattr(svc, "ensure_source_judge_agent", AsyncMock(return_value="ag"))
    client = MagicMock()
    client.run_agent_collect = AsyncMock(return_value={
        "error": None,
        # score over 100 is clamped; a non-int index / missing score is skipped.
        "content": '[{"index":0,"score":150,"reason":"x"},'
                   '{"index":"bad","score":50},{"index":1}]',
    })
    out = await svc.score_sources(client, [
        {"url": "https://a.com", "title": "A", "word_count": 500},
        {"url": "https://b.com", "title": "B", "word_count": 500},
    ])
    assert out == {"https://a.com": (100, "x")}  # clamped; the invalid rows dropped


async def test_evaluate_prunes_low_and_backfills(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    client = MagicMock()
    by_url = {
        "https://good.com/a": {
            "teardown": _teardown("Good", 900), "source_id": "s_good",
            "status": "extracted",
        },
        "https://weak.blog/b": {
            "teardown": _teardown("Weak", 800), "source_id": "s_weak",
            "status": "extracted",
        },
    }
    # 1st score = originals (good high, weak low); 2nd = the backfilled replacement.
    seq = [
        {"https://good.com/a": (88, "authoritative"),
         "https://weak.blog/b": (25, "thin")},
        {"https://fresh.org/c": (80, "reputable")},
    ]
    calls = {"n": 0}

    async def fake_score(_client, _sources):
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    dropped: list[str] = []

    async def fake_drop(_client, _db, _run, source_id):
        dropped.append(source_id)

    async def fake_scrape(_client, url, _titles):
        return {
            "teardown": svc.CompetitorTeardown(
                url=url, title="Fresh", word_count=900, headings=[],
                source_id="s_fresh",
            ),
            "source_id": "s_fresh", "status": "extracted", "url": url,
        }

    monkeypatch.setattr(svc, "score_sources", fake_score)
    monkeypatch.setattr(svc, "_drop_source", fake_drop)
    monkeypatch.setattr(svc, "_scrape_one", fake_scrape)

    teardowns, stats = await svc.evaluate_and_prune(
        client, db, RID, by_url=by_url,
        backfill_pool=["https://fresh.org/c"], title_by_url={},
    )
    # A confirmed replacement (fresh, 80) was scraped BEFORE the weak source was dropped.
    assert dropped == ["s_weak"]  # weak swapped out only once its replacement landed
    assert stats == {"scored": 2, "dropped": 1, "added": 1}
    assert "https://weak.blog/b" not in by_url
    assert "https://fresh.org/c" in by_url  # replacement kept
    assert {t["title"] for t in teardowns} == {"Good", "Fresh"}


async def test_evaluate_no_net_loss_when_backfill_fails(monkeypatch):
    # C2: the destructive delete is deferred until a replacement is CONFIRMED. When every
    # backfill scrape fails, the weak source is NOT dropped — evaluation must never leave
    # the article with fewer usable sources than skipping it (a 45-score source > nothing).
    db = MagicMock()
    db.aexecute = AsyncMock()
    by_url = {
        "https://good.com/a": {
            "teardown": _teardown("Good", 900), "source_id": "s_good",
            "status": "extracted",
        },
        "https://weak.blog/b": {
            "teardown": _teardown("Weak", 800), "source_id": "s_weak",
            "status": "extracted",
        },
    }
    monkeypatch.setattr(svc, "score_sources", AsyncMock(return_value={
        "https://good.com/a": (88, "authoritative"),
        "https://weak.blog/b": (25, "thin"),
    }))
    dropped: list[str] = []
    monkeypatch.setattr(
        svc, "_drop_source",
        AsyncMock(side_effect=lambda *a: dropped.append(a[-1])),
    )
    monkeypatch.setattr(svc, "_scrape_one", AsyncMock(return_value=None))  # all fail
    _, stats = await svc.evaluate_and_prune(
        client=MagicMock(), db=db, run_id=RID, by_url=by_url,
        backfill_pool=["https://fresh.org/c"], title_by_url={},
    )
    assert dropped == []  # nothing deleted without a confirmed replacement
    assert stats == {"scored": 2, "dropped": 0, "added": 0}
    assert "https://weak.blog/b" in by_url  # weak source kept — no net loss


async def test_evaluate_drops_replacement_that_is_also_weak(monkeypatch):
    # A backfilled replacement that itself scores below MIN_TRUST is dropped (not hoarded),
    # and since no replacement was confirmed, the weak original is NOT swapped out either.
    db = MagicMock()
    db.aexecute = AsyncMock()
    by_url = {
        "https://good.com/a": {
            "teardown": _teardown("Good", 900), "source_id": "s_good",
            "status": "extracted",
        },
        "https://weak.blog/b": {
            "teardown": _teardown("Weak", 800), "source_id": "s_weak",
            "status": "extracted",
        },
    }
    seq = [
        {"https://good.com/a": (88, "authoritative"),
         "https://weak.blog/b": (25, "thin")},
        {"https://fresh.org/c": (30, "also thin")},  # replacement also weak
    ]
    calls = {"n": 0}

    async def fake_score(_c, _s):
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    dropped: list[str] = []
    monkeypatch.setattr(svc, "score_sources", fake_score)
    monkeypatch.setattr(
        svc, "_drop_source", AsyncMock(side_effect=lambda *a: dropped.append(a[-1]))
    )
    monkeypatch.setattr(svc, "_scrape_one", AsyncMock(return_value={
        "teardown": svc.CompetitorTeardown(
            url="https://fresh.org/c", title="Fresh", word_count=900, headings=[],
            source_id="s_fresh"),
        "source_id": "s_fresh", "status": "extracted", "url": "https://fresh.org/c",
    }))
    _, stats = await svc.evaluate_and_prune(
        client=MagicMock(), db=db, run_id=RID, by_url=by_url,
        backfill_pool=["https://fresh.org/c"], title_by_url={},
    )
    assert dropped == ["s_fresh"]  # only the also-weak replacement is discarded
    assert stats == {"scored": 2, "dropped": 0, "added": 0}
    assert "https://weak.blog/b" in by_url  # original weak source retained


async def test_evaluate_keeps_all_when_scoring_unavailable(monkeypatch):
    # No scores (judge degraded) → nothing pruned, no backfill, every source survives.
    db = MagicMock()
    db.aexecute = AsyncMock()
    by_url = {
        "https://a.com/x": {
            "teardown": _teardown("A", 800), "source_id": "sa", "status": "extracted",
        },
    }
    monkeypatch.setattr(svc, "score_sources", AsyncMock(return_value={}))
    teardowns, stats = await svc.evaluate_and_prune(
        db=db, client=MagicMock(), run_id=RID, by_url=by_url,
        backfill_pool=["https://b.com/y"], title_by_url={},
    )
    assert stats == {"scored": 0, "dropped": 0, "added": 0}
    assert len(teardowns) == 1  # unscored source is kept, never pruned blind


def make_client() -> TestClient:
    app = create_app()
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}  # assert_brand_access
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


RID = ROW["id"]


async def test_delete_run_skips_shared_source(monkeypatch):
    """Unshared scraped Sources are deleted from Powabase; a Source still referenced
    by another workspace (another run / brand material / cluster) is left intact."""
    db = MagicMock()
    monkeypatch.setattr(svc, "get_run", lambda d, rid: {"id": RID})
    db.fetch_all.return_value = [{"source_id": "shared"}, {"source_id": "solo"}]
    db.fetch_one.return_value = {"id": RID}  # the final run delete
    # 'shared' still referenced elsewhere (>0) → kept; 'solo' (0) → deleted.
    monkeypatch.setattr(
        svc.source_refs, "source_reference_count",
        lambda d, sid, **k: 1 if sid == "shared" else 0,
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    assert await svc.delete_run(client, db, RID) is True
    client.delete_source.assert_awaited_once_with("solo")


async def test_delete_run_dedupes_same_source(monkeypatch):
    """Two URLs in one run that dedupe to the same Powabase Source delete it once."""
    db = MagicMock()
    monkeypatch.setattr(svc, "get_run", lambda d, rid: {"id": RID})
    db.fetch_all.return_value = [{"source_id": "dup"}, {"source_id": "dup"}]
    db.fetch_one.return_value = {"id": RID}
    monkeypatch.setattr(
        svc.source_refs, "source_reference_count", lambda d, sid, **k: 0
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    assert await svc.delete_run(client, db, RID) is True
    client.delete_source.assert_awaited_once_with("dup")


def test_delete_research_route(monkeypatch):
    monkeypatch.setattr(svc, "get_run", lambda db, rid: {"id": RID, "business_id": BID})
    monkeypatch.setattr(svc, "delete_run", AsyncMock(return_value=True))
    resp = make_client().delete(f"/api/research/{RID}")
    assert resp.status_code == 204


def test_create_research_returns_searching(monkeypatch):
    async def fake_task(*args, **kwargs):
        return None

    monkeypatch.setattr(svc, "get_brand", lambda db, bid: {"id": BID, "niche": "x"})
    monkeypatch.setattr(svc, "create_research_run", lambda db, **kw: ROW)
    monkeypatch.setattr(svc, "run_research_task", fake_task)

    client = make_client()
    resp = client.post("/api/research", json={"business_id": BID, "topic": "geo"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "searching"


def test_create_research_unknown_brand_404(monkeypatch):
    monkeypatch.setattr(svc, "get_brand", lambda db, bid: None)
    client = make_client()
    resp = client.post("/api/research", json={"business_id": BID, "topic": "geo"})
    assert resp.status_code == 404
