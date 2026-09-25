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
