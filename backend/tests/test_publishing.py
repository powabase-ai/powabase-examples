"""Publishing/export — rendering (pure), service, and route wiring (hermetic)."""

from unittest.mock import MagicMock

from conftest import with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import publishing as svc

AID = "55555555-5555-5555-5555-555555555555"
ARTICLE = {
    "id": AID,
    "title": "Title",
    "slug": "title",
    "meta_title": "Title",
    "meta_description": "A description.",
    "content_md": "# Heading\n\nBody text.\n\n<script>alert(1)</script>",
    "json_ld": {"@type": "BlogPosting"},
    "keywords": ["kw"],
}


# --- rendering (pure) ---
def test_render_body_strips_scripts():
    h = svc.render_body_html("# A\n\n<script>alert(1)</script>\n\nhi")
    assert "<script" not in h
    assert "<h1>A</h1>" in h


def test_render_standalone_escapes_jsonld():
    doc = svc.render_standalone_html({**ARTICLE, "json_ld": {"x": "</script>"}})
    assert "<!doctype html>" in doc
    assert "\\u003c/script>" in doc  # closing-tag escaped


def test_render_markdown_has_frontmatter():
    out = svc.render_markdown(ARTICLE)
    assert out.startswith("---")
    assert "title:" in out and "# Heading" in out


# --- service ---
async def test_publish_export_marks_published(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": "p1", "article_id": AID, "target_type": "export",
        "status": "success", "created_at": "2026-06-20T00:00:00Z",
    }
    monkeypatch.setattr(svc.gen_svc, "get_article", lambda db, aid: ARTICLE)
    pub = await svc.publish(db, AID, target_type="export", public_base_url="http://x")
    assert pub["status"] == "success"
    sql = " ".join(c.args[0].lower() for c in db.execute.call_args_list)
    assert "status = 'published'" in sql


# --- routes ---
def _client(db, auth: bool = True) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    if auth:
        with_auth(app)
    return TestClient(app)


def test_public_article_is_unauthenticated_and_404s_when_absent(monkeypatch):
    monkeypatch.setattr(svc, "get_published", lambda db, aid: None)
    resp = _client(MagicMock(), auth=False).get(f"/api/public/articles/{AID}")
    assert resp.status_code == 404  # not 401 — the route is public


def test_public_article_returns_published(monkeypatch):
    monkeypatch.setattr(
        svc, "get_published",
        lambda db, aid: {
            "id": AID, "title": "T", "slug": "t", "meta_title": "T",
            "meta_description": "d", "content_html": "<p>x</p>",
            "json_ld": {"@type": "BlogPosting"}, "updated_at": "2026-06-20T00:00:00Z",
        },
    )
    resp = _client(MagicMock(), auth=False).get(f"/api/public/articles/{AID}")
    assert resp.status_code == 200
    assert resp.json()["content_html"] == "<p>x</p>"


def test_publish_route(monkeypatch):
    async def fake_publish(db, aid, **k):
        return {
            "id": "66666666-6666-6666-6666-666666666666",
            "article_id": AID, "target_type": "export",
            "status": "success", "created_at": "2026-06-20T00:00:00Z",
        }

    monkeypatch.setattr(svc, "publish", fake_publish)
    resp = _client(MagicMock()).post(
        f"/api/articles/{AID}/publish", json={"target_type": "export"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_export_route_sets_download_header(monkeypatch):
    monkeypatch.setattr(svc, "export", lambda db, aid, fmt: ("# md", "text/markdown"))
    resp = _client(MagicMock()).get(f"/api/articles/{AID}/export?format=markdown")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
