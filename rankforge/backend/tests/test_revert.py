"""Versions carry frontmatter; revert restores the latest differing version."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import generation as g

CUR = {"id": "a", "content_md": "# T\n\nnew", "title": "T", "meta_title": None,
       "meta_description": "d", "category": "rag", "summary": "s", "faq": None}

AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"


def test_update_article_snapshots_frontmatter_change(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"summary": "changed"})
    ins = db.execute.call_args_list[0].args
    assert "insert into public.article_versions" in ins[0]
    assert "frontmatter" in ins[0]


def test_update_article_wraps_faq_json(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"faq": [{"q": "Q", "a": "A"}]})
    params = db.fetch_one.call_args.args[1]
    assert any(isinstance(p, Json) for p in params)


def test_revert_restores_latest_differing(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    db.fetch_all.return_value = [
        {"id": "v2", "content_md": "# T\n\nnew", "frontmatter": None},  # same body
        {"id": "v1", "content_md": "# T\n\nold",
         "frontmatter": {"summary": "old s", "faq": [{"q": "Q", "a": "A"}]}},
    ]
    called = {}
    monkeypatch.setattr(
        g, "_restore",
        lambda _db, _id, _cur, md, fm: called.setdefault("f", {"content_md": md, **fm}),
    )
    g.revert_last(db, "a")
    assert called["f"]["content_md"] == "# T\n\nold"
    assert called["f"]["summary"] == "old s"


def test_revert_404_when_no_differing_version(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    db.fetch_all.return_value = [{"id": "v", "content_md": CUR["content_md"],
                                  "frontmatter": None}]
    assert g.revert_last(db, "a") is None


# --- route ---
def _brand_db() -> MagicMock:
    """db whose fetch_one satisfies assert_brand_access (org match)."""
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


def test_revert_route_404_when_nothing_to_revert(monkeypatch):
    monkeypatch.setattr(
        g, "get_article", lambda db, aid: dict(CUR, id=AID, business_id=BID)
    )
    monkeypatch.setattr(g, "try_begin_refine", lambda db, aid, total: True)
    monkeypatch.setattr(g, "revert_last", lambda db, aid: None)
    resp = _client(_brand_db()).post(f"/api/articles/{AID}/revert")
    assert resp.status_code == 404


def test_revert_route_409_when_already_in_progress(monkeypatch):
    monkeypatch.setattr(
        g, "get_article", lambda db, aid: dict(CUR, id=AID, business_id=BID)
    )
    monkeypatch.setattr(g, "try_begin_refine", lambda db, aid, total: False)
    resp = _client(_brand_db()).post(f"/api/articles/{AID}/revert")
    assert resp.status_code == 409


def test_revert_route_releases_claim_when_revert_last_raises(monkeypatch):
    """A transient failure inside revert_last (after the claim is committed) must
    not leave the article permanently claimed — the route releases it before the
    exception propagates."""
    monkeypatch.setattr(
        g, "get_article", lambda db, aid: dict(CUR, id=AID, business_id=BID)
    )
    monkeypatch.setattr(g, "try_begin_refine", lambda db, aid, total: True)

    def boom(db, aid):
        raise RuntimeError("transient db error")

    monkeypatch.setattr(g, "revert_last", boom)
    update_calls = []
    monkeypatch.setattr(
        g, "_update", lambda db, aid, **f: update_calls.append(f)
    )

    app = create_app()
    app.dependency_overrides[get_db] = lambda: _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    client = TestClient(with_auth(app), raise_server_exceptions=False)
    resp = client.post(f"/api/articles/{AID}/revert")

    assert resp.status_code == 500
    assert any(c.get("generation_status") == "done" for c in update_calls)


# --- revert/restore write null frontmatter back (final review Issue 2) ---
class _Store:
    """In-memory article + versions, patched in at the generation-service seams
    (get_article / snapshot_version / _update / db.fetch_all|fetch_one)."""

    def __init__(self, monkeypatch, article, versions):
        self.article = dict(article)
        self.versions = list(versions)  # newest first
        self.db = MagicMock()
        self.db.fetch_all.side_effect = lambda sql, params: list(self.versions)
        self.db.fetch_one.side_effect = lambda sql, params: next(
            (v for v in self.versions if v["id"] == params[0]), None
        )
        monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(self.article))
        monkeypatch.setattr(g, "snapshot_version", self._snapshot)
        monkeypatch.setattr(g, "_update", self._update)

    def _snapshot(self, _db, art):
        fm = {k: art.get(k) for k in g.FRONTMATTER_FIELDS}
        self.versions.insert(0, {"id": f"s{len(self.versions)}",
                                 "content_md": art["content_md"], "frontmatter": fm})

    def _update(self, _db, _id, **fields):
        self.article.update(fields)


def test_revert_restores_null_summary(monkeypatch):
    cur = dict(CUR, content_md="# T\n\nbody", summary="hand-written summary")
    v1 = {"id": "v1", "content_md": "# T\n\nbody",
          "frontmatter": {k: cur.get(k) for k in g.FRONTMATTER_FIELDS} | {
              "summary": None}}
    st = _Store(monkeypatch, cur, [v1])
    out = g.revert_last(st.db, "a")
    assert out is not None and out["summary"] is None
    assert st.article["summary"] is None
    # The pre-revert state was versioned, so the revert is itself undoable.
    assert st.versions[0]["frontmatter"]["summary"] == "hand-written summary"


def test_second_revert_does_not_reselect_same_version(monkeypatch):
    cur = dict(CUR, content_md="# T\n\nbody", summary="hand-written summary")
    v1 = {"id": "v1", "content_md": "# T\n\nbody",
          "frontmatter": {k: cur.get(k) for k in g.FRONTMATTER_FIELDS} | {
              "summary": None}}
    st = _Store(monkeypatch, cur, [v1])
    g.revert_last(st.db, "a")
    applied = []
    real = st._update
    monkeypatch.setattr(g, "_update",
                        lambda _db, _id, **f: applied.append(f) or real(_db, _id, **f))
    g.revert_last(st.db, "a")
    # v1 now equals the current state; the second revert steps to the snapshot
    # taken before the first revert (undoing it), not to v1 again.
    assert applied and applied[0]["summary"] == "hand-written summary"


def test_restore_version_writes_null_frontmatter(monkeypatch):
    cur = dict(CUR, content_md="# T\n\nnow", meta_title="Set later",
               faq=[{"q": "Q", "a": "A"}])
    v1 = {"id": "v1", "content_md": "# T\n\nthen",
          "frontmatter": {k: cur.get(k) for k in g.FRONTMATTER_FIELDS} | {
              "meta_title": None, "faq": None}}
    st = _Store(monkeypatch, cur, [v1])
    out = g.restore_version(st.db, "a", "v1")
    assert out["content_md"] == "# T\n\nthen"
    assert out["meta_title"] is None and out["faq"] is None
    assert st.versions[0]["content_md"] == "# T\n\nnow"  # snapshotted first


def test_update_writes_null_faq_as_sql_null():
    db = MagicMock()
    g._update(db, "a", faq=None, summary=None)
    params = db.execute.call_args.args[1]
    assert params[0] is None and params[1] is None


# --- review r1 test gaps: snapshot payload, newest-first revert, route claims ---
def test_snapshot_version_records_frontmatter_payload():
    db = MagicMock()
    art = dict(CUR, faq=[{"q": "Q", "a": "A"}], extra="not recorded")
    g.snapshot_version(db, art)
    sql, params = db.execute.call_args.args
    assert "insert into public.article_versions" in sql
    assert params[0] == "a" and params[1] == CUR["content_md"]
    assert isinstance(params[2], Json)
    assert params[2].obj == {k: art.get(k) for k in g.FRONTMATTER_FIELDS}


def _revert_pick(monkeypatch, rows):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    db.fetch_all.return_value = rows
    picked = {}
    monkeypatch.setattr(
        g, "_restore",
        lambda _db, _id, _cur, md, fm: picked.setdefault("md", md),
    )
    g.revert_last(db, "a")
    return db, picked.get("md")


def test_revert_queries_newest_first(monkeypatch):
    db, _ = _revert_pick(monkeypatch, [])
    sql = " ".join(db.fetch_all.call_args.args[0].lower().split())
    assert "order by created_at desc" in sql and "limit 20" in sql


def test_revert_picks_older_row_when_only_it_differs(monkeypatch):
    rows = [{"id": "v2", "content_md": CUR["content_md"], "frontmatter": None},
            {"id": "v1", "content_md": "# T\n\nolder", "frontmatter": None}]
    assert _revert_pick(monkeypatch, rows)[1] == "# T\n\nolder"


def test_revert_picks_newest_when_both_differ(monkeypatch):
    rows = [{"id": "v2", "content_md": "# T\n\nnewer", "frontmatter": None},
            {"id": "v1", "content_md": "# T\n\nolder", "frontmatter": None}]
    assert _revert_pick(monkeypatch, rows)[1] == "# T\n\nnewer"


def test_revert_route_releases_claim_on_404(monkeypatch):
    monkeypatch.setattr(
        g, "get_article", lambda db, aid: dict(CUR, id=AID, business_id=BID)
    )
    monkeypatch.setattr(g, "try_begin_refine", lambda db, aid, total: True)
    monkeypatch.setattr(g, "revert_last", lambda db, aid: None)
    updates: list = []
    monkeypatch.setattr(g, "_update", lambda db, aid, **f: updates.append(f))
    resp = _client(_brand_db()).post(f"/api/articles/{AID}/revert")
    assert resp.status_code == 404
    assert updates and updates[-1]["generation_status"] == "done"


async def test_revert_rescore_releases_claim_even_when_rescore_fails(monkeypatch):
    from unittest.mock import AsyncMock

    from rankforge_backend.routes import articles as art_routes

    monkeypatch.setattr(art_routes.quality_svc, "reflect",
                        AsyncMock(side_effect=RuntimeError("down")))
    updates: list = []
    monkeypatch.setattr(g, "_update", lambda db, aid, **f: updates.append(f))
    await art_routes._rescore_after_revert(MagicMock(), MagicMock(), AID)
    assert updates[-1]["generation_status"] == "done"


async def test_revert_rescore_releases_claim_after_success(monkeypatch):
    from unittest.mock import AsyncMock

    from rankforge_backend.routes import articles as art_routes

    for mod, name in ((art_routes.quality_svc, "reflect"),
                      (art_routes.geo_svc, "optimize_and_store"),
                      (art_routes.scoring_svc, "score_and_store"),
                      (art_routes.linkcheck_svc, "check_article")):
        monkeypatch.setattr(mod, name, AsyncMock())
    monkeypatch.setattr(g, "get_article",
                        lambda db, aid: dict(CUR, id=AID, business_id=BID))
    updates: list = []
    monkeypatch.setattr(g, "_update", lambda db, aid, **f: updates.append(f))
    await art_routes._rescore_after_revert(MagicMock(), MagicMock(), AID)
    art_routes.scoring_svc.score_and_store.assert_awaited_once()
    assert updates[-1]["generation_status"] == "done"


def test_revert_route_cross_org_404_never_claims(monkeypatch):
    monkeypatch.setattr(
        g, "get_article", lambda db, aid: dict(CUR, id=AID, business_id=BID)
    )
    claims: list = []
    monkeypatch.setattr(g, "try_begin_refine",
                        lambda db, aid, total: claims.append(aid) or True)
    reverts: list = []
    monkeypatch.setattr(g, "revert_last", lambda db, aid: reverts.append(aid))
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID("00000000-0000-0000-0000-0000000000ff")}
    resp = _client(db).post(f"/api/articles/{AID}/revert")
    assert resp.status_code == 404
    assert claims == [] and reverts == []


# --- review r1 I3: nullable frontmatter can be cleared; omitted fields are kept ---
def test_update_article_writes_explicit_null_summary(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"summary": None, "category": None})
    sql, params = db.fetch_one.call_args.args
    assert "summary = %s" in sql and "category = %s" in sql
    assert params[0] is None and params[1] is None


def test_update_article_clears_faq_as_sql_null(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article",
                        lambda _db, _id: dict(CUR, faq=[{"q": "Q", "a": "A"}]))
    g.update_article(db, "a", {"faq": None, "meta_title": None})
    sql, params = db.fetch_one.call_args.args
    assert "faq = %s" in sql and "meta_title = %s" in sql
    assert params[0] is None and params[1] is None  # SQL NULL, not Json(None)


def test_update_article_still_ignores_null_title(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(g, "get_article", lambda _db, _id: dict(CUR))
    g.update_article(db, "a", {"title": None, "summary": "kept"})
    sql = db.fetch_one.call_args.args[0]
    assert "title = %s" not in sql.replace("meta_title", "")


def _patch_route(monkeypatch, body):
    seen = {}

    def _upd(db, aid, fields):
        seen["fields"] = fields
        return dict(CUR, id=AID, business_id=BID, status="draft",
                    generation_status="done", created_at="2026-09-25T00:00:00Z",
                    updated_at="2026-09-25T00:00:00Z")

    monkeypatch.setattr(
        g, "get_article",
        lambda db, aid: dict(CUR, id=AID, business_id=BID, status="draft"),
    )
    monkeypatch.setattr(g, "update_article", _upd)
    resp = _client(_brand_db()).patch(f"/api/articles/{AID}", json=body)
    assert resp.status_code == 200
    return seen["fields"]


def test_patch_null_summary_clears_it(monkeypatch):
    assert _patch_route(monkeypatch, {"summary": None}) == {"summary": None}


def test_patch_omitted_summary_is_left_alone(monkeypatch):
    fields = _patch_route(monkeypatch, {"category": "rag"})
    assert fields == {"category": "rag"}
