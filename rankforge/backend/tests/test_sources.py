"""Sources library — page-meta derivation, bulk delete, and source proxies (hermetic)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import research as svc
from rankforge_backend.services import source_view

BID = "11111111-1111-1111-1111-111111111111"
RID = "22222222-2222-2222-2222-222222222222"
RID2 = "33333333-3333-3333-3333-333333333333"
SID = "src-abc"


def _brand_db():
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db=None, pb=None, user=None):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    app.dependency_overrides[get_powabase] = (
        lambda: pb if pb is not None else MagicMock()
    )
    return TestClient(with_auth(app, user) if user else with_auth(app))


# --- build_page_meta (pure) ---
def test_page_meta_url_source_has_no_pages():
    src = {
        "auto_metadata": {"source_type": "url"},
        "derivatives": {"image": [{"page": 1, "storage_path": "x"}]},
    }
    out = source_view.build_page_meta(src)
    assert out["has_page_images"] is False
    assert out["pages"] == []


def test_page_meta_pdf_source_lists_pages_sorted_with_index():
    src = {
        "auto_metadata": {"source_type": "file", "page_count": 2},
        "derivatives": {
            "image": [
                {"page": 2, "metadata": {"width": 800, "height": 1000}},
                {"page": 1, "metadata": {"width": 800, "height": 1000}},
            ]
        },
    }
    out = source_view.build_page_meta(src)
    assert out["has_page_images"] is True
    assert out["page_count"] == 2
    assert [p["page"] for p in out["pages"]] == [1, 2]
    # index is the position in the original derivative list (the download key).
    assert out["pages"][0]["index"] == 1  # page 1 is at list index 1
    assert out["pages"][1]["index"] == 0


def test_page_meta_empty_source():
    assert source_view.build_page_meta({})["has_page_images"] is False


# --- bulk delete ---
def test_bulk_delete_requires_editor():
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        "/api/sources/bulk-delete", json={"business_id": BID, "row_ids": [RID]}
    )
    assert resp.status_code == 403


def test_bulk_delete_route(monkeypatch):
    monkeypatch.setattr(svc, "bulk_delete_brand_sources", AsyncMock(return_value=2))
    resp = _client().post(
        "/api/sources/bulk-delete",
        json={"business_id": BID, "row_ids": [RID, RID2]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}


async def test_bulk_delete_service_scopes_and_cleans(monkeypatch):
    db = MagicMock()
    db.fetch_all.return_value = [{"id": UUID(RID), "source_id": SID}]
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, s: 0)
    client = MagicMock()
    client.delete_source = AsyncMock()
    n = await svc.bulk_delete_brand_sources(client, db, UUID(BID), [UUID(RID)])
    assert n == 1
    # business_id is the authorization boundary in the select.
    assert "where rr.business_id = %s" in db.fetch_all.call_args.args[0].lower()
    assert any(
        "delete from public.research_sources" in c.args[0]
        for c in db.execute.call_args_list
    )
    client.delete_source.assert_awaited_once_with(SID)


# --- meta ---
def test_source_meta_route(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source = AsyncMock(
        return_value={
            "auto_metadata": {"source_type": "file"},
            "derivatives": {
                "image": [{"page": 1, "metadata": {"width": 800, "height": 1000}}]
            },
        }
    )
    resp = _client(pb=pb).get(f"/api/sources/{SID}/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_page_images"] is True
    assert body["page_count"] == 1


def test_source_meta_404_cross_org(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: False)
    resp = _client().get(f"/api/sources/{SID}/meta")
    assert resp.status_code == 404


# --- page image ---
def test_source_page_image_route(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source_derivative_image = AsyncMock(return_value=(b"\x89PNG", "image/png"))
    resp = _client(pb=pb).get(f"/api/sources/{SID}/pages/0")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG"
    assert resp.headers["cache-control"] == "private, max-age=3600"


def test_source_page_image_404_cross_org(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: False)
    resp = _client().get(f"/api/sources/{SID}/pages/0")
    assert resp.status_code == 404
