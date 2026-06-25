"""Broken-link detection (M6 / Phase 12.3) — checker logic + route wiring."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db
from rankforge_backend.services import generation as gsvc
from rankforge_backend.services import linkcheck

BID = "11111111-1111-1111-1111-111111111111"
AID = "55555555-5555-5555-5555-555555555555"
FID = "88888888-8888-8888-8888-888888888888"
TID = "11111111-1111-1111-1111-111111111111"
ARTICLE = {"id": AID, "business_id": BID, "status": "published"}
BROKEN_ROW = {
    "id": FID, "business_id": BID, "article_id": AID, "url": "https://x.com/404",
    "anchor_text": "x", "kind": "external", "http_status": 404, "reason": "HTTP 404",
    "status": "open", "checked_at": "2026-06-20T00:00:00Z",
    "created_at": "2026-06-20T00:00:00Z",
}


# --- pure helpers ---
def test_extract_links_skips_fenced_code():
    md = "[a](https://a.com)\n```\n[c](https://c.com)\n```\n[d](/p/x)"
    urls = [u for _, u in linkcheck._extract_links(md)]
    assert "https://a.com" in urls
    assert "https://c.com" not in urls  # inside a code block
    assert "/p/x" in urls


def test_internal_reason_flags_unpublished_and_missing():
    db = MagicMock()
    db.fetch_one.return_value = {"status": "published"}
    assert linkcheck._internal_reason(db, "x") is None
    db.fetch_one.return_value = {"status": "draft"}
    assert linkcheck._internal_reason(db, "x")  # not published → broken
    db.fetch_one.return_value = None
    assert linkcheck._internal_reason(db, "x")  # gone → broken


async def test_check_article_flags_unpublished_internal_target(monkeypatch):
    db = MagicMock()
    # 1) internal target status lookup, 2) _record_broken insert ... returning
    db.fetch_one.side_effect = [{"status": "draft"}, BROKEN_ROW]
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"content_md": f"see [guide](/p/{TID})"},
    )
    out = await linkcheck.check_article(db, BID, AID)
    assert out == [BROKEN_ROW]


async def test_check_article_flags_external_4xx(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = BROKEN_ROW  # _record_broken

    async def fake_ext(client, url):
        return 404, "HTTP 404"

    monkeypatch.setattr(linkcheck, "_external_reason", fake_ext)
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"content_md": "see [x](https://example.com/missing)"},
    )
    out = await linkcheck.check_article(db, BID, AID)
    assert out == [BROKEN_ROW]


async def test_check_article_resolves_a_healthy_link(monkeypatch):
    db = MagicMock()

    async def fake_ext(client, url):
        return 200, None

    monkeypatch.setattr(linkcheck, "_external_reason", fake_ext)
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"content_md": "[x](https://ok.example.com)"},
    )
    out = await linkcheck.check_article(db, BID, AID)
    assert out == []
    assert any(
        "status = 'resolved'" in c[0][0] for c in db.execute.call_args_list
    )


# --- routes (hermetic) ---
def _brand_db() -> MagicMock:
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db=None, user: CurrentUser | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    return TestClient(with_auth(app, user) if user else with_auth(app))


def test_list_broken_links_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(linkcheck, "list_findings", lambda d, aid: [BROKEN_ROW])
    resp = _client().get(f"/api/articles/{AID}/links/health")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == FID


def test_check_links_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)

    async def fake_check(d, bid, aid):
        return [BROKEN_ROW]

    monkeypatch.setattr(linkcheck, "check_article", fake_check)
    resp = _client().post(f"/api/articles/{AID}/links/check")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_check_links_requires_editor(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(f"/api/articles/{AID}/links/check")
    assert resp.status_code == 403


def test_ignore_broken_link_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(
        linkcheck, "ignore_finding",
        lambda d, bid, fid: {**BROKEN_ROW, "status": "ignored"},
    )
    resp = _client().post(f"/api/articles/{AID}/links/health/{FID}/ignore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
