"""Research — JSON extraction (unit) + async route wiring (hermetic)."""

from unittest.mock import AsyncMock, MagicMock, call
from uuid import UUID

import httpx
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

    async def fake_scrape(_client, _db, url, _titles, _budget=None):
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
    assert stats == {"scorable": 2, "scored": 2, "dropped": 1, "added": 1}
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
    assert stats == {"scorable": 2, "scored": 2, "dropped": 0, "added": 0}
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
    assert stats == {"scorable": 2, "scored": 2, "dropped": 0, "added": 0}
    assert "https://weak.blog/b" in by_url  # original weak source retained


async def test_evaluate_keeps_all_when_scoring_unavailable(monkeypatch, caplog):
    # No scores (judge degraded) → nothing pruned, no backfill, every source survives.
    db = MagicMock()
    db.aexecute = AsyncMock()
    by_url = {
        "https://a.com/x": {
            "teardown": _teardown("A", 800), "source_id": "sa", "status": "extracted",
        },
    }
    monkeypatch.setattr(svc, "score_sources", AsyncMock(return_value={}))
    with caplog.at_level("WARNING", logger="rankforge.research"):
        teardowns, stats = await svc.evaluate_and_prune(
            db=db, client=MagicMock(), run_id=RID, by_url=by_url,
            backfill_pool=["https://b.com/y"], title_by_url={},
        )
    # scorable=1 but scored=0 → the paid-for evaluation did NOT run; distinct from
    # "nothing to prune" and surfaced at WARNING (not a silent INFO "kept all").
    assert stats == {"scorable": 1, "scored": 0, "dropped": 0, "added": 0}
    assert len(teardowns) == 1  # unscored source is kept, never pruned blind
    assert any(
        "UNEVALUATED" in r.message and r.levelname == "WARNING" for r in caplog.records
    )


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


def test_mark_sources_retrying_empty_returns_early():
    db = MagicMock()
    assert svc.mark_sources_retrying(db, UUID(BID), []) == []
    db.fetch_all.assert_not_called()


def test_mark_sources_retrying_claims_and_returns_rows():
    db = MagicMock()
    claimed = [{"id": RID, "source_id": "old", "url": "https://x.com/a", "title": "A"}]
    db.fetch_all.return_value = claimed
    out = svc.mark_sources_retrying(db, UUID(BID), [UUID(RID)])
    assert out == claimed
    sql, params = db.fetch_all.call_args[0]
    assert "set status = 'retrying'" in sql
    assert "rr.business_id = %s" in sql            # brand-scoped
    assert "is distinct from 'extracted'" in sql   # never re-scrape a good source
    assert "is distinct from 'retrying'" in sql    # double-submit guard
    assert params == (UUID(BID), [UUID(RID)])


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


def test_is_transient_failure_classifies_by_error_message():
    for msg in (
        "Client error '429 Too Many Requests' for url 'https://api.firecrawl.dev/v1/scrape'",
        "Server error '502 Bad Gateway' for url 'https://api.firecrawl.dev/v1/scrape'",
        "503 Service Unavailable",
        "Gateway Timeout",
        "read timed out",
    ):
        assert svc._is_transient_failure(msg), msg
    for msg in (
        "Client error '404 Not Found'",
        "blocked by robots.txt",
        "could not parse document",
        "",
        None,
    ):
        assert not svc._is_transient_failure(msg), msg


async def test_scrape_pacer_spaces_consecutive_starts():
    import asyncio as aio

    pacer = svc._ScrapePacer(0.05)
    loop = aio.get_running_loop()
    start = loop.time()
    await pacer.wait()  # first call returns immediately
    mid = loop.time()
    await pacer.wait()  # second call waits out the interval
    end = loop.time()
    assert mid - start < 0.05          # first didn't block
    assert end - start >= 0.05         # second was spaced by >= interval


def test_scrape_concurrency_value():
    assert svc.SCRAPE_CONCURRENCY == 5


def _failed(msg):
    return {"extraction_status": "failed", "error_message": msg}


_EXTRACTED = {"extraction_status": "extracted", "error_message": None}


def _scrape_client(import_ids, get_source, md="# T\n\nreal words here"):
    """A PowabaseClient mock for scrape tests. `import_ids`: source ids returned by
    successive import_url calls (a fresh id per call models a real re-import). `get_source`:
    a single return value or a list side_effect for the poll(s)."""
    client = MagicMock()
    client.import_url = AsyncMock(
        side_effect=[{"sources": [{"id": i}]} for i in import_ids]
    )
    client.get_source = AsyncMock(
        **({"side_effect": get_source} if isinstance(get_source, list)
           else {"return_value": get_source})
    )
    client.get_source_markdown = AsyncMock(return_value=md)
    client.delete_source = AsyncMock()
    return client


def _patch_scrape_waits(monkeypatch, refcount=0):
    """Patch out the poll/backoff sleep and the pacer so scrape tests run instantly, and
    stub the stale-source ref-count (default 0 → the retry may delete it). Returns
    (sleep_mock, pacer_wait_mock)."""
    sleep = AsyncMock()
    monkeypatch.setattr(svc.asyncio, "sleep", sleep)
    wait = AsyncMock()
    monkeypatch.setattr(svc._scrape_pacer, "wait", wait)
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda db, sid: refcount)
    return sleep, wait


async def test_scrape_one_retries_transient_then_succeeds(monkeypatch):
    _, wait = _patch_scrape_waits(monkeypatch)
    # attempt 1 polls s1 → failed(429); the re-import makes a FRESH source s2 that
    # extracts, and the stale s1 (unreferenced) is deleted so it isn't orphaned.
    client = _scrape_client(
        ["s1", "s2"], [_failed("Client error '429 Too Many Requests'"), _EXTRACTED]
    )
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/a", {})
    assert client.import_url.await_count == 2
    client.delete_source.assert_awaited_once_with("s1")
    assert out["status"] == "extracted"
    assert out["source_id"] == "s2"
    assert out["attempts"] == 2
    assert (out["teardown"].word_count or 0) > 0
    assert wait.await_count == client.import_url.await_count  # pacer gates every import


async def test_scrape_one_does_not_delete_shared_stale_source(monkeypatch):
    # If the stale source is still referenced by another run (ref-count > 0), the retry
    # must NOT delete it — same contract every other delete site honors.
    _, _ = _patch_scrape_waits(monkeypatch, refcount=1)
    client = _scrape_client(
        ["s1", "s2"], [_failed("Client error '429 Too Many Requests'"), _EXTRACTED]
    )
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/a", {})
    client.delete_source.assert_not_awaited()  # s1 is shared — left intact
    assert out["status"] == "extracted" and out["source_id"] == "s2"


async def test_scrape_one_retry_409_dedup_is_safe(monkeypatch):
    # If a re-import 409-dedups back to the SAME source instead of a fresh one, the retry
    # must not delete it and must not crash — new_sid == source_id, so no delete, and it
    # simply re-polls and gives up. (client.py documents the 409-duplicate path.)
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    # first import creates s1; every retry 409-dedups back to s1 (enough for all retries)
    client.import_url = AsyncMock(side_effect=[
        {"sources": [{"id": "s1"}]},
        *([svc.PowabaseError(409, {"duplicate": {"id": "s1"}})] * svc.MAX_SCRAPE_RETRIES),
    ])
    client.get_source = AsyncMock(return_value=_failed("429 Too Many Requests"))
    client.get_source_markdown = AsyncMock(return_value="")
    client.delete_source = AsyncMock()
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/a", {})
    client.delete_source.assert_not_awaited()  # same id → nothing to orphan
    assert out["source_id"] == "s1"
    assert out["status"] == "failed"


async def test_scrape_one_does_not_retry_permanent_failure(monkeypatch):
    _patch_scrape_waits(monkeypatch)
    client = _scrape_client(["s1"], _failed("Client error '404 Not Found'"))
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/dead", {})
    assert client.import_url.await_count == 1
    client.delete_source.assert_not_awaited()
    assert out["status"] == "failed"
    assert out["attempts"] == 1


async def test_scrape_one_gives_up_after_max_retries(monkeypatch):
    sleep, _ = _patch_scrape_waits(monkeypatch)
    client = _scrape_client(["s1", "s2", "s3"], _failed("429 Too Many Requests"))
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/rl", {})
    assert client.import_url.await_count == 1 + svc.MAX_SCRAPE_RETRIES
    # each retry deleted the prior stale source (no orphans left behind)
    assert client.delete_source.await_args_list == [call("s1"), call("s2")]
    assert out["status"] == "failed"
    assert out["attempts"] == 1 + svc.MAX_SCRAPE_RETRIES
    # backoff used the CONFIGURED values, not a mocked-away sleep(0)
    slept = [c.args[0] for c in sleep.await_args_list]
    assert 5.0 in slept and 15.0 in slept


async def test_scrape_one_preserves_status_when_retry_import_fails(monkeypatch):
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    client.import_url = AsyncMock(side_effect=[
        {"sources": [{"id": "s1"}]},
        svc.PowabaseError(402, {"error": "quota exhausted"}),  # retry import fails
    ])
    client.get_source = AsyncMock(return_value=_failed("429 Too Many Requests"))
    client.get_source_markdown = AsyncMock(return_value="")
    client.delete_source = AsyncMock()
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/a", {})
    # the retry's import produced no source → keep the prior failed source + status,
    # never a NULL status, and don't delete the source we fell back to.
    assert out["status"] == "failed"
    assert out["source_id"] == "s1"
    assert out["attempts"] == 2
    client.delete_source.assert_not_awaited()


async def test_scrape_one_respects_retry_budget(monkeypatch):
    _patch_scrape_waits(monkeypatch)
    client = _scrape_client(["s1", "s2", "s3"], _failed("429 Too Many Requests"))
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/rl", {},
                                svc._RetryBudget(1))
    assert client.import_url.await_count == 2  # first attempt + one budgeted retry
    assert out["attempts"] == 2


async def test_scrape_one_clamps_backoff_when_retries_exceed_schedule(monkeypatch):
    # If MAX_SCRAPE_RETRIES is ever bumped past len(RETRY_BACKOFF), PRODUCTION must not
    # IndexError — this exercises the clamp in the code, not just in the test.
    sleep, _ = _patch_scrape_waits(monkeypatch)
    monkeypatch.setattr(svc, "MAX_SCRAPE_RETRIES", 3)  # RETRY_BACKOFF stays len 2
    client = _scrape_client(["s1", "s2", "s3", "s4"], _failed("429 Too Many Requests"))
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/rl", {})
    assert out["attempts"] == 4  # 1 + 3 retries, no IndexError
    slept = [c.args[0] for c in sleep.await_args_list]
    assert slept.count(15.0) >= 2  # 2nd and 3rd retry both clamp to the last backoff


async def test_scrape_attempt_degrades_retryable_poll_error_to_transient(monkeypatch):
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
    client.get_source = AsyncMock(side_effect=svc.PowabaseError(502, "bad gateway"))
    sid, status, md, err = await svc._scrape_attempt(client, "https://x.com/a")
    # a RETRYABLE poll-time upstream error degrades to a transient failure, never raised
    # (which would fail the whole asyncio.gather).
    assert sid == "s1" and status == "failed" and md == ""
    assert svc._is_transient_failure(err)


async def test_scrape_attempt_permanent_poll_error_is_not_retryable(monkeypatch):
    # A 402/401/404 on the poll must NOT become retry-eligible — a paid re-scrape can't
    # fix it, and 402 must never retry (CLAUDE.md). This is the bug the blanket
    # "poll error" marker introduced.
    _patch_scrape_waits(monkeypatch)
    for status_code in (402, 401, 404):
        client = MagicMock()
        client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
        client.get_source = AsyncMock(
            side_effect=svc.PowabaseError(status_code, "nope")
        )
        _sid, status, _md, err = await svc._scrape_attempt(client, "https://x.com/a")
        assert status == "failed"
        assert not svc._is_transient_failure(err), f"{status_code} must not retry"


async def test_scrape_one_does_not_retry_permanent_poll_error(monkeypatch):
    # End-to-end: a 402 on the poll → single attempt, no paid re-scrape.
    _patch_scrape_waits(monkeypatch)
    client = _scrape_client(["s1"], None)
    client.get_source = AsyncMock(side_effect=svc.PowabaseError(402, "quota exhausted"))
    out = await svc._scrape_one(client, MagicMock(), "https://x.com/a", {})
    assert client.import_url.await_count == 1  # NOT 1 + retries
    assert out["attempts"] == 1


async def test_scrape_attempt_import_httpx_error_returns_none(monkeypatch):
    # A raw transport error on import (not a PowabaseError) is caught → no source, logged.
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    client.import_url = AsyncMock(side_effect=httpx.ConnectError("no route"))
    sid, status, _md, _err = await svc._scrape_attempt(client, "https://x.com/a")
    assert sid is None and status is None


async def test_scrape_attempt_markdown_httpx_error_yields_empty(monkeypatch):
    # get_source_markdown bypasses _request, so a raw httpx error is a real path — it is
    # caught and the teardown ends empty rather than the run dying.
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
    client.get_source = AsyncMock(return_value=_EXTRACTED)
    client.get_source_markdown = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    sid, status, md, _err = await svc._scrape_attempt(client, "https://x.com/a")
    assert sid == "s1" and status == "extracted" and md == ""


async def test_scrape_attempt_degrades_transport_error_on_poll(monkeypatch):
    # A raw httpx transport error (not a PowabaseError) on the poll must also be caught,
    # not propagate into asyncio.gather and kill the run.
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
    client.get_source = AsyncMock(side_effect=httpx.ReadTimeout("boom"))
    sid, status, _md, err = await svc._scrape_attempt(client, "https://x.com/a")
    assert sid == "s1" and status == "failed"
    assert svc._is_transient_failure(err)


async def test_scrape_attempt_poll_exhaustion_is_not_retryable(monkeypatch):
    # Still 'extracting' at the poll deadline → return the non-terminal status, NOT a
    # transient 'failed' (which would delete + re-scrape a page about to succeed).
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    client.import_url = AsyncMock(return_value={"sources": [{"id": "s1"}]})
    client.get_source = AsyncMock(
        return_value={"extraction_status": "extracting", "error_message": None}
    )
    sid, status, _md, err = await svc._scrape_attempt(client, "https://x.com/slow")
    assert sid == "s1" and status == "extracting"  # not "failed"
    assert not svc._is_transient_failure(err)       # so _scrape_one won't retry it


async def test_scrape_attempt_returns_none_on_import_failure(monkeypatch):
    _patch_scrape_waits(monkeypatch)
    client = MagicMock()
    client.import_url = AsyncMock(side_effect=svc.PowabaseError(402, {"error": "quota"}))
    sid, status, _md, _err = await svc._scrape_attempt(client, "https://x.com/a")
    assert sid is None and status is None


def test_scrape_summary_counts_ok_recovered_failed():
    results = [
        {"status": "extracted", "attempts": 1},
        {"status": "extracted", "attempts": 3},   # recovered on retry
        {"status": "failed", "attempts": 3},
        None,                                       # import produced no source
    ]
    assert svc._scrape_summary(results) == (2, 1, 2)


def test_scrape_summary_edge_cases():
    assert svc._scrape_summary([]) == (0, 0, 0)
    assert svc._scrape_summary([None, None]) == (0, 0, 2)


def test_retry_backoff_index_is_clamped():
    bo = svc.RETRY_BACKOFF
    assert [bo[min(i, len(bo) - 1)] for i in range(svc.MAX_SCRAPE_RETRIES)] == [5.0, 15.0]
    assert bo[min(5, len(bo) - 1)] == 15.0  # a future MAX_SCRAPE_RETRIES bump won't IndexError


FAILED_ROW = {"id": RID, "source_id": "old", "url": "https://x.com/a", "title": "A"}


def _scrape_result(source_id, status, word_count):
    td = svc.CompetitorTeardown(
        url="https://x.com/a", title="A", word_count=word_count, source_id=source_id
    )
    return {
        "teardown": td, "status": status, "source_id": source_id,
        "url": "https://x.com/a", "attempts": 1, "excerpt": "",
    }


async def test_retry_brand_sources_success_adopts_and_deletes_old(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 0)
    monkeypatch.setattr(
        svc, "_scrape_one",
        AsyncMock(return_value=_scrape_result("new", "extracted", 321)),
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    await svc.retry_brand_sources(client, db, UUID(BID), [dict(FAILED_ROW)])

    upd = db.aexecute.await_args_list[0].args
    assert "set source_id = %s" in upd[0]
    assert upd[1] == ("new", 321, "extracted", RID)   # row now points at the fresh Source
    client.delete_source.assert_awaited_once_with("old")  # stale orphan removed


async def test_retry_brand_sources_keeps_shared_old_source(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 1)
    monkeypatch.setattr(
        svc, "_scrape_one",
        AsyncMock(return_value=_scrape_result("new", "extracted", 200)),
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    await svc.retry_brand_sources(client, db, UUID(BID), [dict(FAILED_ROW)])

    client.delete_source.assert_not_awaited()  # old Source still referenced → kept


async def test_retry_brand_sources_none_restores_failed(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc, "_scrape_one", AsyncMock(return_value=None))
    client = MagicMock()
    client.delete_source = AsyncMock()

    await svc.retry_brand_sources(client, db, UUID(BID), [dict(FAILED_ROW)])

    upd = db.aexecute.await_args_list[0].args
    assert "set status = 'failed'" in upd[0]
    assert upd[1] == (RID,)                       # linkage unchanged, back to failed
    client.delete_source.assert_not_awaited()


async def test_retry_brand_sources_failed_dict_adopts_and_deletes_old(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 0)
    # re-import succeeded but extraction failed → dict with a fresh source_id, status 'failed'
    monkeypatch.setattr(
        svc, "_scrape_one",
        AsyncMock(return_value=_scrape_result("newfail", "failed", None)),
    )
    client = MagicMock()
    client.delete_source = AsyncMock()

    await svc.retry_brand_sources(client, db, UUID(BID), [dict(FAILED_ROW)])

    upd = db.aexecute.await_args_list[0].args
    assert upd[1] == ("newfail", None, "failed", RID)   # adopted the fresh failed source
    client.delete_source.assert_awaited_once_with("old")  # old orphan removed


async def test_retry_brand_sources_one_failure_does_not_sink_batch(monkeypatch):
    db = MagicMock()
    db.aexecute = AsyncMock()
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 0)

    async def scrape(client, d, url, tbu, budget=None):
        if url.endswith("boom"):
            raise RuntimeError("kaboom")
        return _scrape_result("new2", "extracted", 250)

    monkeypatch.setattr(svc, "_scrape_one", scrape)
    client = MagicMock()
    client.delete_source = AsyncMock()
    boom = {"id": "44444444-4444-4444-4444-444444444444",
            "source_id": "o1", "url": "https://x.com/boom", "title": "b"}
    ok = {"id": RID, "source_id": "o2", "url": "https://x.com/ok", "title": "ok"}

    await svc.retry_brand_sources(client, db, UUID(BID), [boom, ok])

    calls = [c.args for c in db.aexecute.await_args_list]
    # the good row was updated to extracted; the boom row's scrape error was caught,
    # logged, and the row reset to 'failed' by the outer except. The key: batch survived.
    assert any(a[1] == ("new2", 250, "extracted", RID) for a in calls)
    assert any("set status = 'failed'" in a[0] and a[1] == (boom["id"],) for a in calls)


async def test_retry_brand_sources_db_error_does_not_sink_batch(monkeypatch):
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, sid, **k: 0)
    monkeypatch.setattr(
        svc, "_scrape_one",
        AsyncMock(side_effect=lambda *a, **k: _scrape_result("new", "extracted", 200)),
    )
    boom_id = "44444444-4444-4444-4444-444444444444"

    async def aexec(sql, params):
        if params and params[-1] == boom_id:   # row_id is the last param in both updates
            raise RuntimeError("db down")

    db = MagicMock()
    db.aexecute = AsyncMock(side_effect=aexec)
    client = MagicMock()
    client.delete_source = AsyncMock()
    boom = {"id": boom_id, "source_id": "o1", "url": "https://x.com/a", "title": "b"}
    ok = {"id": RID, "source_id": "o2", "url": "https://x.com/a", "title": "ok"}

    # Must NOT raise even though the boom row's UPDATE errors.
    await svc.retry_brand_sources(client, db, UUID(BID), [boom, ok])

    # The sibling row still got its success UPDATE (batch survived).
    assert any(c.args[1] == ("new", 200, "extracted", RID) for c in db.aexecute.await_args_list)


def test_retry_sources_route_queues_claimed_rows(monkeypatch):
    claimed = [{"id": RID, "source_id": "old", "url": "https://x.com/a", "title": "A"}]
    monkeypatch.setattr(svc, "mark_sources_retrying", lambda db, bid, ids: claimed)
    monkeypatch.setattr(svc, "retry_brand_sources", AsyncMock())
    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        coro.close()  # we asserted dispatch; don't actually run the worker

    monkeypatch.setattr("rankforge_backend.routes.sources.spawn", fake_spawn)
    resp = make_client().post(
        "/api/sources/retry", json={"business_id": BID, "row_ids": [RID]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 1}
    assert len(spawned) == 1


def test_retry_sources_route_no_claimed_rows_no_spawn(monkeypatch):
    monkeypatch.setattr(svc, "mark_sources_retrying", lambda db, bid, ids: [])
    spawned = []
    monkeypatch.setattr(
        "rankforge_backend.routes.sources.spawn", lambda c: spawned.append(c)
    )
    resp = make_client().post(
        "/api/sources/retry", json={"business_id": BID, "row_ids": [RID]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 0}
    assert not spawned
