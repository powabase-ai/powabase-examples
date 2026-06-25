"""Brand materials (M6) — discovery/tracking (unit) + ingestion/route wiring.

Mocks at the Database / Powabase boundary; no real network or DB calls.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.powabase import PowabaseError
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


# --- discovery + tracking (unit) ---
async def test_discover_crawl_imports_and_tracks(monkeypatch):
    """crawl mode → client.import_urls("crawl", url=…) → each source tracked."""
    db = MagicMock()
    db.fetch_one.return_value = {"id": RID}  # _track_source insert
    client = MagicMock()
    client.import_urls = AsyncMock(
        return_value=[
            {"id": "src_a", "url": "https://brand.example/a"},
            {"id": "src_b", "url": "https://brand.example/b"},
        ]
    )
    n = await svc._discover_and_track(
        client, db, BID, {"sitemap_url": None},
        mode="crawl", url="https://brand.example", extra_urls=(), max_pages=30,
    )
    assert n == 2
    client.import_urls.assert_awaited_once_with(
        "crawl", url="https://brand.example", max_pages=30
    )
    assert db.fetch_one.call_count == 2  # one tracked row per imported source


async def test_discover_sitemap_falls_back_to_brand_url():
    """sitemap mode with no explicit url uses the brand's saved sitemap_url."""
    db = MagicMock()
    db.fetch_one.return_value = {"id": RID}
    client = MagicMock()
    client.import_urls = AsyncMock(return_value=[])
    await svc._discover_and_track(
        client, db, BID, {"sitemap_url": "https://brand.example/sitemap.xml"},
        mode="sitemap", url=None, extra_urls=(), max_pages=30,
    )
    client.import_urls.assert_awaited_once_with(
        "sitemap", url="https://brand.example/sitemap.xml", max_pages=30
    )


async def test_discover_noops_when_nothing_to_discover():
    """No crawl url and no brand sitemap → no platform call, nothing tracked."""
    db = MagicMock()
    client = MagicMock()
    client.import_urls = AsyncMock()
    n = await svc._discover_and_track(
        client, db, BID, {"sitemap_url": None},
        mode="sitemap", url=None, extra_urls=(), max_pages=30,
    )
    assert n == 0
    client.import_urls.assert_not_awaited()


# --- discover_pages (crawl preview, unit) ---
def test_discover_pages_keeps_same_site_and_subdomains(monkeypatch):
    monkeypatch.setattr(svc, "_is_public_host", lambda host: True)
    root = "https://brand.example"
    html = (
        '<a href="/about">a</a>'
        '<a href="https://docs.brand.example/guide">d</a>'
        '<a href="https://other.com/x">o</a>'
        '<a href="https://brand.example/pricing">p</a>'
        '<a href="/_next/static/app.js">asset</a>'
    )

    class FakeStream:
        def __init__(self, text, code=200, ctype="text/html"):
            self._text = text
            self.status_code = code
            self.is_redirect = False
            self.headers = {"content-type": ctype}
            self.encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self):
            yield self._text.encode()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url):
            return FakeStream(html) if url == root else FakeStream("", 404)

    monkeypatch.setattr(svc.httpx, "Client", FakeClient)
    out = svc.discover_pages(root, 50)
    assert "https://brand.example/about" in out
    assert "https://docs.brand.example/guide" in out  # subdomain kept
    assert "https://brand.example/pricing" in out
    assert all("other.com" not in u for u in out)  # cross-site dropped
    assert all("_next" not in u for u in out)  # assets dropped


def test_is_public_host_blocks_internal():
    assert not svc._is_public_host("localhost")
    assert not svc._is_public_host("127.0.0.1")
    assert not svc._is_public_host("10.0.0.1")
    assert not svc._is_public_host("192.168.0.5")
    assert not svc._is_public_host("169.254.169.254")  # cloud metadata
    assert not svc._is_public_host("100.64.0.1")  # CGNAT (100.64/10)
    assert not svc._is_public_host("[::1]:443")  # IPv6 loopback literal
    assert not svc._is_public_host("intranet.local")
    assert svc._is_public_host("8.8.8.8")  # public IP literal


def test_discover_pages_rejects_private_host():
    # SSRF guard short-circuits before any network fetch — no httpx mock needed.
    assert svc.discover_pages("http://127.0.0.1/admin", 10) == []
    assert svc.discover_pages("http://169.254.169.254/latest/meta-data", 10) == []


def test_discover_route_groups_by_host(monkeypatch):
    monkeypatch.setattr(
        svc,
        "discover_pages",
        lambda url, max_pages: [
            "https://brand.example/a",
            "https://docs.brand.example/x",
            "https://brand.example/b",
        ],
    )
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/discover",
        json={"url": "https://brand.example"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    hosts = {h["host"]: h["urls"] for h in body["hosts"]}
    assert set(hosts) == {"brand.example", "docs.brand.example"}
    assert len(hosts["brand.example"]) == 2


# --- _ensure_indexing (one-time chunk-config migration) ---
async def test_ensure_indexing_noop_when_already_tuned():
    client = MagicMock()
    client.get_kb = AsyncMock(
        return_value={
            "indexing_config": {
                "chunk_size": svc.MATERIALS_CHUNK_SIZE,
                "overlap": svc.MATERIALS_OVERLAP,
                "strategy": "chunk_embed",
            }
        }
    )
    client.update_kb = AsyncMock()
    client.reindex_kb = AsyncMock()
    await svc._ensure_indexing(client, "kb1")
    client.update_kb.assert_not_awaited()
    client.reindex_kb.assert_not_awaited()


async def test_ensure_indexing_patches_full_config_and_reindexes_on_drift():
    client = MagicMock()
    client.get_kb = AsyncMock(
        return_value={
            "indexing_config": {
                "chunk_size": 2000,
                "overlap": 50,
                "strategy": "chunk_embed",
                "embedding_model": "text-embedding-3-small",
            }
        }
    )
    client.update_kb = AsyncMock()
    client.reindex_kb = AsyncMock()
    await svc._ensure_indexing(client, "kb1")
    cfg = client.update_kb.await_args.kwargs["indexing_config"]
    # new chunk_size/overlap, with strategy + embedding_model preserved (PATCH replaces)
    assert cfg["chunk_size"] == svc.MATERIALS_CHUNK_SIZE
    assert cfg["overlap"] == svc.MATERIALS_OVERLAP
    assert cfg["strategy"] == "chunk_embed"
    assert cfg["embedding_model"] == "text-embedding-3-small"
    client.reindex_kb.assert_awaited_once_with("kb1")


# --- _track_source SQL shape (unit) ---
def test_track_source_inserts_with_source_id():
    db = MagicMock()
    db.fetch_one.return_value = {"id": RID}
    svc._track_source(
        db, BID, url="  https://a.example/x  ", source_id="src_1", origin="crawl"
    )
    query, params = db.fetch_one.call_args[0]
    assert "insert into public.brand_sources" in query
    assert "on conflict do nothing" in query
    # trimmed; carries the source_id + origin
    assert params == (BID, "https://a.example/x", "crawl", "src_1")


def test_track_source_skips_empty_url():
    db = MagicMock()
    svc._track_source(db, BID, url="   ", source_id="src_1", origin="crawl")
    db.fetch_one.assert_not_called()


# --- list_sources SQL shape (unit) ---
def test_list_sources_sql_shape():
    db = MagicMock()
    db.fetch_all.return_value = [SOURCE]
    out = svc.list_sources(db, BID)
    assert out == [SOURCE]
    query, params = db.fetch_all.call_args[0]
    assert "from public.brand_sources" in query
    assert "order by created_at desc" in query
    assert params == (BID,)


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
    captured = {}

    async def fake_ingest(client, db, business_id, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(svc, "ingest", fake_ingest)
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/ingest",
        json={"mode": "crawl", "url": "https://brand.example", "max_pages": 15},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"
    # the route threads the mode/url/max_pages through to the worker
    assert captured["mode"] == "crawl"
    assert captured["url"] == "https://brand.example"
    assert captured["max_pages"] == 15


def test_ingest_requires_editor(monkeypatch):
    async def fake_ingest(*a, **k):
        return None

    monkeypatch.setattr(svc, "ingest", fake_ingest)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/business-profiles/{BID}/materials/ingest", json={"mode": "sitemap"}
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


def test_get_content_502_on_upstream_error(monkeypatch):
    """A source-service failure surfaces as 502 (not a misleading 404)."""
    async def fake_content(c, d, bid, rid):
        raise PowabaseError(502, {"message": "bad upstream"})

    monkeypatch.setattr(svc, "source_content", fake_content)
    resp = _client().get(f"/api/business-profiles/{BID}/materials/{RID}/content")
    assert resp.status_code == 502


# --- refresh / bulk-delete routes (hermetic) ---
def test_refresh_is_202_and_threads_row_ids(monkeypatch):
    captured = {}

    async def fake_refresh(c, d, bid, row_ids):
        captured["row_ids"] = row_ids

    monkeypatch.setattr(svc, "refresh_sources", fake_refresh)
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/refresh", json={"row_ids": [RID]}
    )
    assert resp.status_code == 202
    assert [str(x) for x in captured["row_ids"]] == [RID]


def test_refresh_requires_editor(monkeypatch):
    async def fake_refresh(*a, **k):
        return None

    monkeypatch.setattr(svc, "refresh_sources", fake_refresh)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/business-profiles/{BID}/materials/refresh", json={"row_ids": [RID]}
    )
    assert resp.status_code == 403


def test_bulk_delete_is_202_and_threads_row_ids(monkeypatch):
    captured = {}

    async def fake_remove(c, d, bid, row_ids):
        captured["row_ids"] = row_ids

    monkeypatch.setattr(svc, "remove_sources", fake_remove)
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/bulk-delete", json={"row_ids": [RID]}
    )
    assert resp.status_code == 202
    assert [str(x) for x in captured["row_ids"]] == [RID]


def test_bulk_action_rejects_empty_selection():
    resp = _client().post(
        f"/api/business-profiles/{BID}/materials/refresh", json={"row_ids": []}
    )
    assert resp.status_code == 422  # row_ids min_length=1


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


async def test_source_content_propagates_upstream_error():
    """An upstream failure (e.g. a 502 from the source service) must NOT be masked as
    'no content' — it propagates so the route can report it honestly."""
    db = MagicMock()
    db.fetch_one.return_value = {"source_id": "src_1"}
    client = MagicMock()
    client.get_source_markdown = AsyncMock(
        side_effect=PowabaseError(502, {"message": "bad upstream"})
    )
    with pytest.raises(PowabaseError):
        await svc.source_content(client, db, BID, RID)


# --- refresh / bulk-delete (unit) ---
async def test_refresh_one_reimports_a_url_row(monkeypatch):
    """A URL-backed row is de-indexed + its stale Source deleted, then re-imported
    fresh (source_id cleared) so a changed page is actually re-scraped."""
    db = MagicMock()
    db.aexecute = AsyncMock()
    client = MagicMock()
    client.remove_source_from_kb = AsyncMock()
    client.delete_source = AsyncMock()
    seen = {}

    async def fake_import_one(c, d, kb, row, already):
        seen["row"] = row
        seen["already"] = already

    monkeypatch.setattr(svc, "_import_one", fake_import_one)
    row = {"id": RID, "source_id": "src_old",
           "url": "https://brand.example/x", "title": "T"}
    assert await svc._refresh_one(client, db, "kb_1", row, {"src_old": "idx_old"})
    client.remove_source_from_kb.assert_awaited_once_with("kb_1", "idx_old")
    client.delete_source.assert_awaited_once_with("src_old")
    assert seen["row"]["source_id"] is None  # re-imports fresh, not the stale source
    assert seen["already"] == set()  # so it re-adds to the KB


async def test_refresh_one_skips_file_upload_row():
    """An uploaded file (no http URL) can't be re-scraped — skip it, touch nothing."""
    client = MagicMock()
    client.delete_source = AsyncMock()
    row = {"id": RID, "source_id": "src_f", "url": "brand-deck.pdf", "title": "deck"}
    assert await svc._refresh_one(client, MagicMock(), "kb_1", row, {}) is False
    client.delete_source.assert_not_awaited()


async def test_refresh_sources_noop_when_only_files_selected(monkeypatch):
    db = MagicMock()
    db.fetch_all.return_value = [
        {"id": RID, "source_id": "s", "url": "deck.pdf",
         "title": "d", "status": "extracted", "origin": "manual"},
    ]
    progress: list[tuple[str, str]] = []
    monkeypatch.setattr(
        svc, "_set_progress",
        lambda d, b, phase, msg, **k: progress.append((phase, msg)),
    )
    monkeypatch.setattr(svc, "ensure_materials_kb", AsyncMock(return_value="kb_1"))
    await svc.refresh_sources(MagicMock(), db, BID, [UUID(RID)])
    assert progress[-1][0] == "done"
    assert "Nothing to refresh" in progress[-1][1]


async def test_remove_sources_counts_and_finishes(monkeypatch):
    db = MagicMock()
    progress: list[tuple[str, str]] = []
    monkeypatch.setattr(
        svc, "_set_progress",
        lambda d, b, phase, msg, **k: progress.append((phase, msg)),
    )
    monkeypatch.setattr(svc, "remove_source", AsyncMock(return_value=True))
    await svc.remove_sources(MagicMock(), db, BID, [UUID(RID), UUID(BID)])
    assert progress[-1][0] == "done"
    assert "Removed 2 pages" in progress[-1][1]
