"""Publishing/export — rendering (pure), service, and route wiring (hermetic)."""

from unittest.mock import MagicMock
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import publishing as svc

AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"
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


# --- webhook SSRF guard ---
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "https://10.0.0.1/hook",
        "https://192.168.1.5/hook",
        "https://169.254.169.254/latest/meta-data",
        "ftp://example.com/x",
        "https://",
    ],
)
def test_validate_webhook_url_blocks_private_and_bad_scheme(url):
    with pytest.raises(ValueError):
        svc.validate_webhook_url(url)


def test_validate_webhook_url_allows_public():
    svc.validate_webhook_url("https://8.8.8.8/hook")  # public IP — no raise


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


def _brand_db() -> MagicMock:
    """db whose fetch_one yields an article in the caller's org (passes the
    gen_svc.get_article lookup + assert_brand_access in the publish routes)."""
    db = MagicMock()
    db.fetch_one.return_value = {**ARTICLE, "business_id": BID, "org_id": UUID(ADMIN_ORG)}
    return db


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


def test_public_article_renders_fresh_from_markdown(monkeypatch):
    # The route renders content_html from content_md at read time (never stale).
    monkeypatch.setattr(
        svc, "get_published",
        lambda db, aid: {
            "id": AID, "title": "T", "slug": "t", "meta_title": "T",
            "meta_description": "d", "content_md": "# Live Heading\n\nbody",
            "json_ld": {"@type": "BlogPosting"}, "updated_at": "2026-06-20T00:00:00Z",
        },
    )
    resp = _client(MagicMock(), auth=False).get(f"/api/public/articles/{AID}")
    assert resp.status_code == 200
    assert "<h1>Live Heading</h1>" in resp.json()["content_html"]


def test_publish_requires_editor(monkeypatch):
    from rankforge_backend.models.profile import CurrentUser

    async def fake_publish(db, aid, **k):
        return None  # shouldn't be reached

    monkeypatch.setattr(svc, "publish", fake_publish)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    with_auth(app, CurrentUser(id=AID, role="writer", org_id=ADMIN_ORG))
    resp = TestClient(app).post(
        f"/api/articles/{AID}/publish", json={"target_type": "export"}
    )
    assert resp.status_code == 403


def test_publish_route(monkeypatch):
    async def fake_publish(db, aid, **k):
        return {
            "id": "66666666-6666-6666-6666-666666666666",
            "article_id": AID, "target_type": "export",
            "status": "success", "created_at": "2026-06-20T00:00:00Z",
        }

    monkeypatch.setattr(svc, "publish", fake_publish)
    resp = _client(_brand_db()).post(
        f"/api/articles/{AID}/publish", json={"target_type": "export"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_export_route_sets_download_header(monkeypatch):
    monkeypatch.setattr(svc, "export", lambda db, aid, fmt: ("# md", "text/markdown"))
    resp = _client(_brand_db()).get(f"/api/articles/{AID}/export?format=markdown")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
