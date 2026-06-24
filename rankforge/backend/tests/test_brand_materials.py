"""Brand materials (M6) — sitemap parsing (unit) + ingestion/route wiring (hermetic).

Mocks at the Database / Powabase boundary; no real network or DB calls.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import brand_materials as svc

BID = "11111111-1111-1111-1111-111111111111"
RID = "99999999-9999-9999-9999-999999999999"

SOURCE = {
    "id": RID,
    "source_id": "src_1",
    "url": "https://brand.example/docs/intro",
    "title": "Intro",
    "status": "extracted",
    "origin": "sitemap",
    "created_at": "2026-06-20T00:00:00Z",
}


# --- sitemap_urls (unit) ---
def test_sitemap_urls_parses_urlset(monkeypatch):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://brand.example/blog/post-1</loc></url>
      <url><loc>https://brand.example/docs/setup</loc></url>
      <url><loc>https://brand.example/tag/news</loc></url>
      <url><loc>https://brand.example/logo.png</loc></url>
      <url><loc>https://brand.example/about</loc></url>
    </urlset>"""

    class FakeResp:
        text = xml

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    urls = svc.sitemap_urls("https://brand.example/sitemap.xml")
    # noise (tag, .png) dropped
    assert "https://brand.example/tag/news" not in urls
    assert "https://brand.example/logo.png" not in urls
    # content pages kept and ranked first
    assert "https://brand.example/blog/post-1" in urls
    assert "https://brand.example/docs/setup" in urls
    assert urls.index("https://brand.example/blog/post-1") < urls.index(
        "https://brand.example/about"
    )


def test_sitemap_urls_resilient_on_failure(monkeypatch):
    class BoomClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "Client", BoomClient)
    assert svc.sitemap_urls("https://brand.example/sitemap.xml") == []


def test_sitemap_urls_empty_when_no_url():
    assert svc.sitemap_urls("") == []


# --- list_sources / add_urls SQL shape (unit) ---
def test_list_sources_sql_shape():
    db = MagicMock()
    db.fetch_all.return_value = [SOURCE]
    out = svc.list_sources(db, BID)
    assert out == [SOURCE]
    query, params = db.fetch_all.call_args[0]
    assert "from public.brand_sources" in query
    assert "order by created_at desc" in query
    assert params == (BID,)


def test_add_urls_inserts_dedups_and_skips_empty():
    db = MagicMock()
    # first url inserts (returns a row), second conflicts (None), blanks skipped
    db.fetch_one.side_effect = [{"id": RID}, None]
    n = svc.add_urls(
        db, BID, ["  https://a.example/x  ", "https://a.example/x", "", "   "], "manual"
    )
    assert n == 1
    assert db.fetch_one.call_count == 2  # only the two non-empty urls hit the DB
    query, params = db.fetch_one.call_args_list[0][0]
    assert "insert into public.brand_sources" in query
    assert "on conflict do nothing" in query
    # trimmed before insert
    assert params == (BID, "https://a.example/x", "manual")


async def test_remove_source_cascades_kb_then_source_then_row(monkeypatch):
    """Cascade: remove_source_from_kb → delete_source → delete the tracking row."""
    db = MagicMock()
    # afetch_one: 1) fetch the row (id+source_id), 2) the delete...returning row
    db.afetch_one = AsyncMock(
        side_effect=[{"id": RID, "source_id": "src_1"}, {"id": RID}]
    )
    monkeypatch.setattr(
        svc.brands, "get_profile", lambda d, bid: {"materials_kb_id": "kb_1"}
    )
    client = MagicMock()
    client.remove_source_from_kb = AsyncMock()
    client.delete_source = AsyncMock()

    assert await svc.remove_source(client, db, BID, RID) is True
    client.remove_source_from_kb.assert_awaited_once_with("kb_1", "src_1")
    client.delete_source.assert_awaited_once_with("src_1")
    # the final afetch_one is the delete...returning
    last_q = db.afetch_one.await_args_list[-1][0][0]
    assert "delete from public.brand_sources" in last_q


async def test_remove_source_false_when_missing():
    db = MagicMock()
    db.afetch_one = AsyncMock(return_value=None)
    assert await svc.remove_source(MagicMock(), db, BID, RID) is False


async def test_remove_source_kb_failure_still_deletes_row(monkeypatch):
    """A KB-removal failure must not block the row delete (best-effort cascade)."""
    db = MagicMock()
    db.afetch_one = AsyncMock(
        side_effect=[{"id": RID, "source_id": "src_1"}, {"id": RID}]
    )
    monkeypatch.setattr(
        svc.brands, "get_profile", lambda d, bid: {"materials_kb_id": "kb_1"}
    )
    client = MagicMock()
    client.remove_source_from_kb = AsyncMock(side_effect=RuntimeError("boom"))
    client.delete_source = AsyncMock(side_effect=RuntimeError("boom"))
    assert await svc.remove_source(client, db, BID, RID) is True


# --- routes (hermetic) ---
def _brand_db() -> MagicMock:
    """A db mock whose fetch_one satisfies assert_brand_access (org match)."""
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db=None, user: CurrentUser | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    with_auth(app, user) if user else with_auth(app)
    return TestClient(app)


def test_get_materials_returns_sources_and_progress(monkeypatch):
    db = _brand_db()
    monkeypatch.setattr(svc, "list_sources", lambda d, bid: [SOURCE])
    monkeypatch.setattr(
        svc.brands,
        "get_profile",
        lambda d, bid: {
            "org_id": UUID(ADMIN_ORG),
            "materials_kb_id": "kb_1",
            "materials_progress": {"phase": "done", "message": "2 indexed."},
        },
    )
    resp = _client(db=db).get(f"/api/business-profiles/{BID}/materials")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["sources"]) == 1
    assert body["progress"]["phase"] == "done"
    assert body["kb_ready"] is True


def test_ingest_is_202(monkeypatch):
    monkeypatch.setattr(svc, "add_urls", lambda d, bid, urls, origin: len(urls))

    async def fake_ingest(*a, **k):
        return None

    monkeypatch.setattr(svc, "ingest", fake_ingest)
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/ingest",
        json={"urls": ["https://brand.example/pricing"]},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"


def test_ingest_requires_editor(monkeypatch):
    monkeypatch.setattr(svc, "add_urls", lambda d, bid, urls, origin: 0)

    async def fake_ingest(*a, **k):
        return None

    monkeypatch.setattr(svc, "ingest", fake_ingest)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/business-profiles/{BID}/materials/ingest", json={"urls": []}
    )
    assert resp.status_code == 403


def test_delete_material_404_when_missing(monkeypatch):
    async def fake_remove(c, d, bid, rid):
        return False

    monkeypatch.setattr(svc, "remove_source", fake_remove)
    resp = _client().delete(f"/api/business-profiles/{BID}/materials/{RID}")
    assert resp.status_code == 404


def test_delete_material_204_when_removed(monkeypatch):
    async def fake_remove(c, d, bid, rid):
        return True

    monkeypatch.setattr(svc, "remove_source", fake_remove)
    resp = _client().delete(f"/api/business-profiles/{BID}/materials/{RID}")
    assert resp.status_code == 204


# --- upload route (hermetic) ---
def test_upload_is_202_for_editor(monkeypatch):
    async def fake_ingest_file(*a, **k):
        return None

    monkeypatch.setattr(svc, "ingest_file", fake_ingest_file)
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/upload",
        files={"file": ("brand.pdf", b"%PDF-1.4 hello", "application/pdf")},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"


def test_upload_requires_editor(monkeypatch):
    async def fake_ingest_file(*a, **k):
        return None

    monkeypatch.setattr(svc, "ingest_file", fake_ingest_file)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/business-profiles/{BID}/materials/upload",
        files={"file": ("brand.pdf", b"%PDF-1.4 hello", "application/pdf")},
    )
    assert resp.status_code == 403


def test_upload_too_large_is_413(monkeypatch):
    async def fake_ingest_file(*a, **k):  # pragma: no cover — should never run
        return None

    monkeypatch.setattr(svc, "ingest_file", fake_ingest_file)
    big = b"x" * (20 * 1024 * 1024 + 1)
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/upload",
        files={"file": ("big.pdf", big, "application/pdf")},
    )
    assert resp.status_code == 413


# --- content route (hermetic) ---
def test_get_content_returns_markdown(monkeypatch):
    async def fake_content(c, d, bid, rid):
        return "# Hello\n\nbrand body"

    monkeypatch.setattr(svc, "source_content", fake_content)
    resp = _client().get(f"/api/business-profiles/{BID}/materials/{RID}/content")
    assert resp.status_code == 200
    assert resp.json()["content"] == "# Hello\n\nbrand body"


def test_get_content_404_when_none(monkeypatch):
    async def fake_content(c, d, bid, rid):
        return None

    monkeypatch.setattr(svc, "source_content", fake_content)
    resp = _client().get(f"/api/business-profiles/{BID}/materials/{RID}/content")
    assert resp.status_code == 404


# --- source_content service (hermetic) ---
async def test_source_content_returns_none_without_source_id():
    db = MagicMock()
    db.fetch_one.return_value = {"source_id": None}
    out = await svc.source_content(MagicMock(), db, BID, RID)
    assert out is None


async def test_source_content_fetches_markdown():
    db = MagicMock()
    db.fetch_one.return_value = {"source_id": "src_1"}
    client = MagicMock()
    client.get_source_markdown = AsyncMock(return_value="# md")
    out = await svc.source_content(client, db, BID, RID)
    assert out == "# md"
    client.get_source_markdown.assert_awaited_once_with("src_1")
