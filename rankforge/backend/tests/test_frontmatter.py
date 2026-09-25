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

    # The real fix_meta runs; only its agent is faked (review r2 over-mocking).
    monkeypatch.setattr(revise, "ensure_meta_agent", AsyncMock(return_value="m"))
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
    assert flags == ["model's summary was 2 words (needs 40-60) — left empty"]
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
    c = _client({"meta_title": "New meta"})
    state, calls = await _run_complete_with(monkeypatch, art, c)
    c.run_agent.assert_awaited_once()  # the meta model only: no summary/FAQ call
    assert calls[0][0] == "snapshot" and ("update", ["meta_title"]) in calls
    assert state["art"]["meta_title"] == "New meta"


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
        "model's summary was 1 words (needs 40-60) — left empty"
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

    # The real fix_meta runs; only its agent is faked (review r2 over-mocking).
    monkeypatch.setattr(revise, "ensure_meta_agent", AsyncMock(return_value="m"))
    await fm.complete(client, MagicMock(), "a")
    return state, calls


# --- review r1 I4 / K1: POST /frontmatter returns the remaining export issues and
# claims the article for the duration of the fix ---
_ROUTE: dict = {}  # the last _fm_route's article state, for fake complete()s


def _fm_route(monkeypatch, art, *, claim=True, complete=None):
    from rankforge_backend.services import generation as g

    state = {"art": {"status": "draft", "generation_status": "done",
                     "created_at": "2026-09-25T00:00:00Z",
                     "updated_at": "2026-09-25T00:00:00Z",
                     **art, "id": AID, "business_id": BID}}
    _ROUTE["state"] = state
    updates: list = []
    monkeypatch.setattr(g, "get_article", lambda d, a: dict(state["art"]))
    monkeypatch.setattr(brands_svc, "get_profile", lambda d, b: BRAND)
    monkeypatch.setattr(g, "try_begin_refine", lambda d, a, total: claim)
    monkeypatch.setattr(g, "_update", lambda d, a, **f: updates.append(f))
    monkeypatch.setattr(fm, "complete", complete or AsyncMock(return_value=[]))
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return _route_client(db), updates


def test_frontmatter_route_returns_remaining_export_issues(monkeypatch):
    art = {**ART, "title": "T", "summary": S45, "faq": GOOD["faq"], "category": None,
           "generation_status": "done"}
    client, updates = _fm_route(monkeypatch, art)
    resp = client.post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 200
    body = resp.json()
    assert body["article"]["id"] == AID
    assert body["export_issues"] == ["category is missing"]
    assert updates[-1]["generation_status"] == "done"  # claim released


def test_frontmatter_route_409_when_busy(monkeypatch):
    complete = AsyncMock()
    client, updates = _fm_route(monkeypatch, ART, claim=False, complete=complete)
    assert client.post(f"/api/articles/{AID}/frontmatter").status_code == 409
    complete.assert_not_awaited()
    assert updates == []


def test_frontmatter_route_releases_claim_when_complete_raises(monkeypatch):
    client, updates = _fm_route(
        monkeypatch, {**ART, "generation_status": "done"},
        complete=AsyncMock(side_effect=RuntimeError("boom")),
    )
    client = TestClient(client.app, raise_server_exceptions=False)
    assert client.post(f"/api/articles/{AID}/frontmatter").status_code == 500
    assert updates and updates[-1]["generation_status"] == "done"


def test_frontmatter_route_keeps_a_failed_article_failed(monkeypatch):
    client, updates = _fm_route(
        monkeypatch, {**ART, "generation_status": "failed",
                      "progress": {"phase": "failed"}},
    )
    assert client.post(f"/api/articles/{AID}/frontmatter").status_code == 200
    assert updates[-1]["generation_status"] == "failed"


# --- review r2: the REAL fix_meta / enforce_meta version the article before their
# first write (only the agent and the DB are faked) ---
async def _run_complete_real(monkeypatch, art, client, brand=BRAND, **kw):
    from rankforge_backend.services import revise

    state = {"art": dict(art)}
    calls: list = []
    monkeypatch.setattr(fm.gen_svc, "get_article", lambda d, a: dict(state["art"]))
    monkeypatch.setattr(fm.brands, "get_profile", lambda d, b: brand)
    monkeypatch.setattr(fm, "ensure_agent", AsyncMock(return_value="agent"))
    monkeypatch.setattr(revise, "ensure_meta_agent", AsyncMock(return_value="meta"))

    def _upd(d, a, **f):
        calls.append(("update", sorted(f)))
        state["art"].update(f)

    monkeypatch.setattr(fm.gen_svc, "_update", _upd)
    monkeypatch.setattr(
        fm.gen_svc, "snapshot_version",
        lambda d, article: calls.append(("snapshot", article.get("meta_title"))),
    )
    flags = await fm.complete(client, MagicMock(), "a", **kw)
    return state, calls, flags


VALID = {**ART, "summary": S45, "faq": GOOD["faq"], "category": "rag"}


async def test_real_fix_meta_snapshots_before_its_write(monkeypatch):
    art = {**VALID, "title": "t" * 80, "meta_title": "m" * 70}
    c = _client({"meta_title": "Short meta", "meta_description": "A description."})
    state, calls, _ = await _run_complete_real(monkeypatch, art, c)
    assert calls[0] == ("snapshot", "m" * 70)
    assert ("update", ["meta_description", "meta_title"]) in calls
    assert state["art"]["meta_title"] == "Short meta"


async def test_real_enforce_meta_snapshots_before_its_write(monkeypatch):
    # The meta model gives nothing usable, so only the deterministic clamp writes.
    art = {**VALID, "title": "t" * 80, "meta_title": None}
    c = _raw_client("not json")
    state, calls, _ = await _run_complete_real(monkeypatch, art, c)
    assert calls == [("snapshot", None), ("update", ["meta_title"])]
    assert 0 < len(state["art"]["meta_title"]) <= 60


FAQ_BODY = "# T\n\n## Intro\n\ntext\n\n## Frequently asked questions\n\n### Q?\n\nA."
NO_FAQ_BRAND = {**BRAND, "blog_profile": {**PROF, "faq": {"enabled": False}}}


async def test_complete_keeps_body_faq_when_profile_faq_is_disabled(monkeypatch):
    art = {**VALID, "content_md": FAQ_BODY, "faq": None}
    c = _client()
    state, calls, _ = await _run_complete_real(monkeypatch, art, c, NO_FAQ_BRAND)
    assert calls == [] and state["art"]["content_md"] == FAQ_BODY
    c.run_agent.assert_not_called()


async def test_complete_body_faq_only_snapshots_before_writing(monkeypatch):
    art = {**VALID, "content_md": FAQ_BODY}
    state, calls, _ = await _run_complete_real(monkeypatch, art, _client())
    assert calls == [("snapshot", None), ("update", ["content_md"])]
    assert "Frequently asked" not in state["art"]["content_md"]


# --- review r2: rule boundaries ---
P = BlogProfile.model_validate(PROF)


@pytest.mark.parametrize("art,over", [
    ({"title": "t" * 60}, False),
    ({"title": "t" * 61}, True),
    ({"title": "t" * 61, "meta_title": "m" * 60}, False),
    ({"title": "T", "meta_title": "m" * 60}, False),
    ({"title": "T", "meta_title": "m" * 61}, True),
    ({"title": "T", "meta_description": "d" * 160}, False),
    ({"title": "T", "meta_description": "d" * 161}, True),
])
def test_meta_over_limits_boundaries(art, over):
    assert fm.meta_over_limits(art, P) is over


def _faq(n):
    return [{"q": f"Q{i}?", "a": "A."} for i in range(n)]


@pytest.mark.parametrize("field,value,fails", [
    ("faq", _faq(2), True), ("faq", _faq(3), False), ("faq", _faq(6), False),
    ("faq", _faq(7), True), ("faq", _faq(9), True),
    ("category", "AI agents", True), ("category", None, True),
    ("category", "agents", False),
    ("summary", " ".join(["w"] * 39), True), ("summary", " ".join(["w"] * 40), False),
    ("summary", " ".join(["w"] * 60), False), ("summary", " ".join(["w"] * 61), True),
])
def test_failing_fields_boundaries(field, value, fails):
    art = {**VALID, field: value}
    assert (field in fm.failing_fields(art, P)) is fails
    assert fm.failing_fields(art, P) <= {field}


# --- review r2 N4 / K8: force regenerates summary + FAQ even when they pass ---
NEW_S = " ".join(["fresh"] * 44) + " end."
NEW = {"category": "agents", "summary": NEW_S, "faq": _faq(5)}


async def test_complete_without_force_leaves_valid_fields(monkeypatch):
    c = _client(NEW)
    state, calls, _ = await _run_complete_real(monkeypatch, VALID, c)
    assert calls == [] and state["art"]["summary"] == S45
    c.run_agent.assert_not_called()


async def test_complete_force_regenerates_valid_summary_and_faq(monkeypatch):
    c = _client(NEW)
    state, calls, flags = await _run_complete_real(
        monkeypatch, VALID, c, force=True
    )
    assert flags == []
    assert state["art"]["summary"] == NEW_S and state["art"]["faq"] == _faq(5)
    # A valid stored category (perhaps picked by hand) is never replaced by a
    # forced Generate, whose button only mentions summary and FAQ.
    assert state["art"]["category"] == "rag"
    assert calls == [("snapshot", None), ("update", ["faq", "summary"])]


async def test_complete_force_never_writes_invalid_values(monkeypatch):
    bad = {"category": "AI agents", "summary": "too short", "faq": _faq(1)}
    art = {**VALID, "category": "agents"}  # the fallback key would be "rag"
    state, calls, flags = await _run_complete_real(
        monkeypatch, art, _client(bad, bad), force=True
    )
    assert calls == []  # nothing passed the rules: no write, no version
    assert state["art"]["summary"] == S45 and state["art"]["faq"] == GOOD["faq"]
    assert state["art"]["category"] == "agents"  # a valid category isn't redone
    assert flags == [
        "model's summary was 2 words (needs 40-60) — kept the stored one",
        "model's FAQ had 1 item(s) (needs 3-6) — kept the stored one",
    ]


async def test_complete_force_leaves_category_to_the_cluster(monkeypatch):
    monkeypatch.setattr(fm.clusters_svc, "get_cluster",
                        lambda d, cid: {"id": cid, "category": "rag"})
    c = _client(NEW)
    art = {**VALID, "cluster_id": "c1"}
    state, calls, _ = await _run_complete_real(monkeypatch, art, c, force=True)
    assert state["art"]["category"] == "rag"
    assert ("update", ["faq", "summary"]) in calls


# --- review r2 minor: a defaulted category is flagged ---
async def test_generate_flags_a_defaulted_category(deps):
    flags = await fm.generate(_raw_client("nope", "still nope"), MagicMock(), "a")
    key = fm.blog_rules.fallback_category(P, None)
    assert f"category defaulted to {key}" in flags
    assert _written(deps)["category"] == key


# --- review r2 N4 / K8: the route takes {"force": bool} and reports `changed` ---
def test_frontmatter_route_force_defaults_to_false(monkeypatch):
    complete = AsyncMock(return_value=[])
    client, _ = _fm_route(monkeypatch, VALID, complete=complete)
    assert client.post(f"/api/articles/{AID}/frontmatter").status_code == 200
    assert complete.await_args.kwargs.get("force") is False
    resp = client.post(f"/api/articles/{AID}/frontmatter", json={})
    assert resp.status_code == 200
    assert complete.await_args.kwargs.get("force") is False


def test_frontmatter_route_passes_force(monkeypatch):
    complete = AsyncMock(return_value=[])
    client, _ = _fm_route(monkeypatch, VALID, complete=complete)
    resp = client.post(f"/api/articles/{AID}/frontmatter", json={"force": True})
    assert resp.status_code == 200
    assert complete.await_args.kwargs.get("force") is True


def test_frontmatter_route_reports_changed_fields(monkeypatch):
    async def _complete(pb, db, aid, *, force=False):
        # Rewrite the summary; re-save the same FAQ (keys in jsonb order) and the
        # same category — neither is a change.
        art = _ROUTE["state"]["art"]
        art.update(summary=NEW_S, category=art["category"],
                   faq=[{"a": x["a"], "q": x["q"]} for x in art["faq"]])
        return []

    client, _ = _fm_route(monkeypatch, VALID, complete=_complete)
    resp = client.post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 200
    assert resp.json()["changed"] == ["summary"]


def test_frontmatter_route_changed_is_empty_when_nothing_moved(monkeypatch):
    client, _ = _fm_route(monkeypatch, VALID)
    resp = client.post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 200 and resp.json()["changed"] == []


# --- review r2 N2 / K9: an invalid stored profile is not "no profile" ---
def test_frontmatter_route_409_names_the_invalid_profile(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": AID, "business_id": BID, "org_id": UUID(ADMIN_ORG),
    }
    bad = {**PROF, "link": {"min": 1}}  # misspelled key
    monkeypatch.setattr(
        brands_svc, "get_profile", lambda d, bid: {"id": BID, "blog_profile": bad}
    )
    resp = _route_client(db).post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("blog profile is invalid: ")
    assert "link" in resp.json()["detail"]


def test_frontmatter_route_409_without_profile_says_so(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": AID, "business_id": BID, "org_id": UUID(ADMIN_ORG),
    }
    monkeypatch.setattr(
        brands_svc, "get_profile", lambda d, bid: {"blog_profile": None}
    )
    resp = _route_client(db).post(f"/api/articles/{AID}/frontmatter")
    assert resp.json()["detail"] == "brand has no blog profile"


# --- coordinator r2 follow-ups ---
def test_frontmatter_route_releases_claim_when_the_before_read_fails(monkeypatch):
    from rankforge_backend.services import generation as g

    client, updates = _fm_route(monkeypatch, VALID)
    n = {"calls": 0}

    def _get(d, a):
        n["calls"] += 1
        if n["calls"] == 2:  # 1 = the guard; 2 = the read right after the claim
            raise RuntimeError("db hiccup")
        return dict(_ROUTE["state"]["art"])

    monkeypatch.setattr(g, "get_article", _get)
    client = TestClient(client.app, raise_server_exceptions=False)
    assert client.post(f"/api/articles/{AID}/frontmatter").status_code == 500
    assert updates and updates[-1]["generation_status"] == "done"


@pytest.mark.parametrize("force", ["yes", 1, "true"])
def test_frontmatter_route_rejects_a_non_bool_force(monkeypatch, force):
    complete = AsyncMock(return_value=[])
    client, _ = _fm_route(monkeypatch, VALID, complete=complete)
    resp = client.post(f"/api/articles/{AID}/frontmatter", json={"force": force})
    assert resp.status_code == 422
    complete.assert_not_awaited()


# --- review r3 I-1 / K12: POST /frontmatter returns the step's flags ---
def test_frontmatter_route_returns_the_flags(monkeypatch):
    complete = AsyncMock(return_value=["category defaulted to rag"])
    client, _ = _fm_route(monkeypatch, VALID, complete=complete)
    resp = client.post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 200
    assert resp.json()["flags"] == ["category defaulted to rag"]


def test_frontmatter_route_flags_are_empty_when_none(monkeypatch):
    client, _ = _fm_route(monkeypatch, VALID)
    resp = client.post(f"/api/articles/{AID}/frontmatter")
    assert resp.status_code == 200 and resp.json()["flags"] == []


def _real_route(monkeypatch, art, pb):
    """The route with the REAL complete(): only the agents and the DB are faked,
    and writes land in the article state so `changed` sees them."""
    from rankforge_backend.routes.deps import get_powabase
    from rankforge_backend.services import generation as g

    client, _ = _fm_route(monkeypatch, art, complete=fm.complete)
    state = _ROUTE["state"]
    monkeypatch.setattr(g, "_update", lambda d, a, **f: state["art"].update(f))
    monkeypatch.setattr(g, "snapshot_version", lambda d, article: None)
    monkeypatch.setattr(fm, "ensure_agent", AsyncMock(return_value="agent"))
    client.app.dependency_overrides[get_powabase] = lambda: pb
    return client


def test_forced_generate_reports_a_kept_summary(monkeypatch):
    """Review probe: stored summary valid, the model returns 3 words twice. The
    stored summary is kept, `changed` doesn't claim it, and the flag says why."""
    c = _client({"summary": "Too short here.", "faq": _faq(5)},
                {"summary": "Too short here.", "faq": _faq(5)})
    client = _real_route(monkeypatch, VALID, c)
    resp = client.post(f"/api/articles/{AID}/frontmatter", json={"force": True})
    body = resp.json()
    assert resp.status_code == 200 and body["changed"] == ["faq"]
    assert body["flags"] == [
        "model's summary was 3 words (needs 40-60) — kept the stored one"
    ]
    assert body["article"]["summary"] == S45


def test_fix_automatically_reports_a_defaulted_category(monkeypatch):
    c = _raw_client(json.dumps({"category": "AI agents"}),
                    json.dumps({"category": "AI agents"}))
    client = _real_route(monkeypatch, {**VALID, "category": None}, c)
    resp = client.post(f"/api/articles/{AID}/frontmatter")
    body = resp.json()
    key = fm.blog_rules.fallback_category(P, None)
    assert body["changed"] == ["category"] and body["article"]["category"] == key
    assert body["flags"] == [f"category defaulted to {key}"]


# --- review r3 K13 / F12: a body-FAQ-only fix reports content_md ---
def test_frontmatter_route_reports_a_body_faq_strip(monkeypatch):
    client = _real_route(monkeypatch, {**VALID, "content_md": FAQ_BODY}, _client())
    resp = client.post(f"/api/articles/{AID}/frontmatter")
    body = resp.json()
    assert body["changed"] == ["content_md"] and body["flags"] == []
    assert "Frequently asked" not in body["article"]["content_md"]


# --- review r3 minors ---
async def test_forced_generate_that_changes_nothing_writes_nothing(monkeypatch):
    """The model returns exactly the stored summary and FAQ: no write, no version."""
    same = {"category": "rag", "summary": S45,
            "faq": [{"a": x["a"], "q": x["q"]} for x in GOOD["faq"]]}
    state, calls, flags = await _run_complete_real(
        monkeypatch, VALID, _client(same), force=True
    )
    assert calls == [] and flags == []


async def test_fix_meta_equal_to_the_stored_meta_writes_nothing(monkeypatch):
    from rankforge_backend.services import revise

    monkeypatch.setattr(revise, "ensure_meta_agent", AsyncMock(return_value="m"))
    upd, before = MagicMock(), MagicMock()
    monkeypatch.setattr(revise.gen_svc, "_update", upd)
    art = {**ART, "meta_title": "Stored meta", "meta_description": "Stored desc."}
    c = _client({"meta_title": "Stored meta", "meta_description": "Stored desc."})
    await revise.fix_meta(c, MagicMock(), "a", art, {}, before_write=before)
    upd.assert_not_called()
    before.assert_not_called()
    c2 = _client({"meta_title": "Stored meta", "meta_description": "New desc."})
    await revise.fix_meta(c2, MagicMock(), "a", art, {}, before_write=before)
    assert upd.call_args.kwargs == {"meta_description": "New desc."}


async def test_generate_keeps_a_valid_stored_category_over_the_fallback(deps):
    deps_art = {**ART, "category": "agents"}
    with patch.object(fm.gen_svc, "get_article", return_value=deps_art):
        flags = await fm.generate(_raw_client("nope", "nope"), MagicMock(), "a")
    assert "category kept as agents (the model's was not a listed key)" in flags
    assert "category" not in _written(deps)


async def test_complete_regenerates_a_failing_category_when_forced(monkeypatch):
    c = _client(NEW)
    art = {**VALID, "category": "AI agents"}
    state, _, _ = await _run_complete_real(monkeypatch, art, c, force=True)
    assert state["art"]["category"] == "agents"


@pytest.mark.parametrize("stored,tail", [
    (None, "left empty"), ("", "left empty"), ("   ", "left empty"),
    ([], "left empty"), ("old summary", "kept the stored one"),
    (_faq(1), "kept the stored one"),
])
def test_rejected_flag_says_kept_only_when_something_was_stored(stored, tail):
    assert fm.rejected_flag("summary", "a b c", P, stored).endswith(f"— {tail}")
