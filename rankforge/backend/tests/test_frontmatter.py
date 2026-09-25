"""frontmatter — category/summary/FAQ generation, meta enforcement."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import business_profiles as brands_svc
from rankforge_backend.services import frontmatter as fm

PROF = BlogProfile.model_validate({
    "categories": [{"key": "rag", "label": "R"}, {"key": "agents", "label": "A"}],
    "stance": "favor_brand",
}).model_dump()
BRAND = {"id": "b", "name": "Powabase", "blog_profile": PROF}
ART = {"id": "a", "business_id": "b", "title": "How to build RAG", "cluster_id": None,
       "content_md": "# T\n\nbody", "meta_title": None, "meta_description": "d"}
S45 = " ".join(["word"] * 44) + " end."
GOOD = {"category": "agents", "summary": S45,
        "faq": [{"q": f"Q{i}?", "a": "A."} for i in range(4)]}


def _client(*payloads):
    c = MagicMock()
    c.run_agent = AsyncMock(side_effect=[{"content": json.dumps(p)} for p in payloads])
    return c


@pytest.fixture
def deps():
    with patch.object(fm.gen_svc, "get_article", return_value=dict(ART)), \
         patch.object(fm.brands, "get_profile", return_value=BRAND), \
         patch.object(fm.gen_svc, "_update") as upd, \
         patch.object(fm, "ensure_agent", AsyncMock(return_value="agent")):
        yield upd


async def test_generate_writes_fields(deps):
    flags = await fm.generate(_client(GOOD), MagicMock(), "a")
    assert flags == []
    kw = deps.call_args.kwargs
    assert kw["category"] == "agents" and kw["summary"] == S45 and len(kw["faq"]) == 4


async def test_generate_retries_once_on_flags(deps):
    bad = {**GOOD, "summary": "too short"}
    c = _client(bad, GOOD)
    flags = await fm.generate(c, MagicMock(), "a")
    assert flags == [] and c.run_agent.await_count == 2
    assert "summary is 2 words" in c.run_agent.await_args_list[1].args[1]


async def test_generate_noop_without_profile(deps):
    with patch.object(fm.brands, "get_profile", return_value={"blog_profile": None}):
        c = _client()
        assert await fm.generate(c, MagicMock(), "a") == []
        c.run_agent.assert_not_called()


def test_enforce_meta_sets_meta_title_for_long_h1():
    with patch.object(fm.gen_svc, "_update") as upd:
        art = {**ART, "title": "x " * 40, "meta_title": "y" * 80,
               "meta_description": "z" * 200}
        fm.enforce_meta(MagicMock(), "a", art, BlogProfile.model_validate(PROF))
        kw = upd.call_args.kwargs
        assert len(kw["meta_title"]) <= 60 and len(kw["meta_description"]) <= 160


def test_enforce_meta_derives_meta_title_from_title_when_missing():
    with patch.object(fm.gen_svc, "_update") as upd:
        art = {**ART, "title": "word " * 20, "meta_title": None}
        fm.enforce_meta(MagicMock(), "a", art, BlogProfile.model_validate(PROF))
        assert 0 < len(upd.call_args.kwargs["meta_title"]) <= 60


# --- route ---
AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"


def _route_client(db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    with_auth(app)
    return TestClient(app)


def test_frontmatter_route_409_without_profile(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": AID, "business_id": BID, "org_id": UUID(ADMIN_ORG),
    }
    monkeypatch.setattr(
        brands_svc, "get_profile", lambda d, bid: {"blog_profile": None}
    )
    resp = _route_client(db).post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 409


# --- complete(): undo point + body-FAQ strip (final review #4/#5) ---
async def _run_complete(monkeypatch, art):
    state = {"art": dict(art)}
    calls: list = []
    monkeypatch.setattr(fm.gen_svc, "get_article", lambda d, a: dict(state["art"]))
    monkeypatch.setattr(fm.brands, "get_profile", lambda d, b: BRAND)
    monkeypatch.setattr(fm, "ensure_agent", AsyncMock(return_value="agent"))

    def _upd(d, a, **f):
        calls.append(("update", sorted(f)))
        state["art"].update(f)

    def _snap(d, article):
        calls.append(("snapshot", article.get("summary")))

    monkeypatch.setattr(fm.gen_svc, "_update", _upd)
    monkeypatch.setattr(fm.gen_svc, "snapshot_version", _snap)
    from rankforge_backend.services import revise

    async def _fix_meta(client, db, aid, article, brief, **k):
        _upd(db, aid, meta_title="New meta")

    monkeypatch.setattr(revise, "fix_meta", _fix_meta)
    await fm.complete(_client(GOOD), MagicMock(), "a")
    return state, calls


async def test_complete_snapshots_once_before_first_write(monkeypatch):
    _, calls = await _run_complete(monkeypatch, {**ART, "summary": "hand edit"})
    snaps = [c for c in calls if c[0] == "snapshot"]
    assert snaps == [("snapshot", "hand edit")]
    assert calls[0][0] == "snapshot"  # before any write


async def test_complete_strips_body_faq(monkeypatch):
    body = "# T\n\n## Intro\n\ntext\n\n## Frequently asked questions\n\n### Q?\n\nA."
    state, _ = await _run_complete(monkeypatch, {**ART, "content_md": body})
    assert "Frequently asked" not in state["art"]["content_md"]
    assert "## Intro" in state["art"]["content_md"]


# --- review r1 C2: a bad retry / unparseable reply never overwrites good values ---
def _raw_client(*contents):
    c = MagicMock()
    c.run_agent = AsyncMock(side_effect=[{"content": x} for x in contents])
    return c


def _written(upd) -> dict:
    return {k: v for c in upd.call_args_list for k, v in c.kwargs.items()}


async def test_generate_unparseable_reply_is_a_failed_attempt_not_a_500(deps):
    c = _raw_client("not json at all", json.dumps(GOOD))
    flags = await fm.generate(c, MagicMock(), "a")
    assert flags == [] and c.run_agent.await_count == 2
    assert _written(deps)["summary"] == S45


async def test_generate_unparseable_twice_writes_nothing(deps):
    flags = await fm.generate(_raw_client("nope", "still nope"), MagicMock(), "a")
    assert any("not valid JSON" in f for f in flags)
    assert "summary" not in _written(deps) and "faq" not in _written(deps)


async def test_generate_keeps_the_better_first_attempt(deps):
    first = {**GOOD, "summary": "too short"}  # 1 flag
    flags = await fm.generate(_client(first, {}), MagicMock(), "a")  # retry: 2 flags
    w = _written(deps)
    assert flags == ["summary is 2 words (needs 40-60)"]
    assert len(w["faq"]) == 4  # the first attempt's valid FAQ is kept
    assert "summary" not in w  # an out-of-bounds summary is never written


async def test_generate_first_attempt_wins_ties(deps):
    first = {**GOOD, "summary": "too short"}
    second = {**GOOD, "faq": [{"q": "only?", "a": "one"}], "category": "rag"}
    await fm.generate(_client(first, second), MagicMock(), "a")
    w = _written(deps)
    assert w["category"] == "agents" and len(w["faq"]) == 4


async def test_generate_empty_reply_never_clears_stored_values(monkeypatch):
    stored = {**ART, "summary": S45, "faq": GOOD["faq"], "category": None}
    monkeypatch.setattr(fm.gen_svc, "get_article", lambda d, a: dict(stored))
    monkeypatch.setattr(fm.brands, "get_profile", lambda d, b: BRAND)
    monkeypatch.setattr(fm, "ensure_agent", AsyncMock(return_value="agent"))
    upd = MagicMock()
    monkeypatch.setattr(fm.gen_svc, "_update", upd)
    await fm.generate(_client({}, {}), MagicMock(), "a")
    w = _written(upd)
    assert "summary" not in w and "faq" not in w


async def test_complete_regenerates_only_failing_fields(monkeypatch):
    art = {**ART, "summary": S45, "faq": GOOD["faq"], "category": None}
    c = _client({"category": "rag", "summary": "x", "faq": []})
    state, calls = await _run_complete_with(monkeypatch, art, c)
    assert state["art"]["summary"] == S45 and state["art"]["faq"] == GOOD["faq"]
    assert state["art"]["category"] == "rag"
    assert ("update", ["category"]) in calls


async def test_complete_noop_when_everything_passes(monkeypatch):
    art = {**ART, "summary": S45, "faq": GOOD["faq"], "category": "rag"}
    c = _client()
    state, calls = await _run_complete_with(monkeypatch, art, c)
    assert calls == []  # no snapshot, no write
    c.run_agent.assert_not_called()


async def test_complete_long_title_fixes_meta_only(monkeypatch):
    art = {**ART, "title": "t" * 80, "summary": S45, "faq": GOOD["faq"],
           "category": "rag"}
    c = _client()
    state, calls = await _run_complete_with(monkeypatch, art, c)
    c.run_agent.assert_not_called()  # summary/FAQ are not regenerated
    assert calls[0][0] == "snapshot" and ("update", ["meta_title"]) in calls


async def test_complete_no_snapshot_when_the_model_writes_nothing(monkeypatch):
    art = {**ART, "summary": S45, "faq": [{"q": "Q?", "a": "A."}], "category": "rag"}
    c = _raw_client("nope", "nope")
    state, calls = await _run_complete_with(monkeypatch, art, c)
    assert calls == []


async def test_complete_returns_flags(monkeypatch):
    art = {**ART, "summary": None, "faq": GOOD["faq"], "category": "rag"}
    monkeypatch.setattr(fm.gen_svc, "get_article", lambda d, a: dict(art))
    monkeypatch.setattr(fm.brands, "get_profile", lambda d, b: BRAND)
    monkeypatch.setattr(fm, "ensure_agent", AsyncMock(return_value="agent"))
    monkeypatch.setattr(fm.gen_svc, "_update", MagicMock())
    monkeypatch.setattr(fm.gen_svc, "snapshot_version", MagicMock())
    c = _client({"summary": "short"}, {"summary": "short"})
    assert await fm.complete(c, MagicMock(), "a") == [
        "summary is 1 words (needs 40-60)"
    ]


async def _run_complete_with(monkeypatch, art, client):
    state = {"art": dict(art)}
    calls: list = []
    monkeypatch.setattr(fm.gen_svc, "get_article", lambda d, a: dict(state["art"]))
    monkeypatch.setattr(fm.brands, "get_profile", lambda d, b: BRAND)
    monkeypatch.setattr(fm, "ensure_agent", AsyncMock(return_value="agent"))

    def _upd(d, a, **f):
        calls.append(("update", sorted(f)))
        state["art"].update(f)

    monkeypatch.setattr(fm.gen_svc, "_update", _upd)
    monkeypatch.setattr(
        fm.gen_svc, "snapshot_version",
        lambda d, article: calls.append(("snapshot", article.get("summary"))),
    )
    from rankforge_backend.services import revise

    async def _fix_meta(client, db, aid, article, brief, *, before_write=None, **k):
        if before_write:
            before_write()
        _upd(db, aid, meta_title="New meta")

    monkeypatch.setattr(revise, "fix_meta", _fix_meta)
    await fm.complete(client, MagicMock(), "a")
    return state, calls
