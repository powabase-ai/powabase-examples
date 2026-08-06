"""Sources library — page-meta derivation, bulk delete, and source proxies (hermetic)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.powabase import PowabaseError
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
    sqls = [c.args[0].lower() for c in db.fetch_all.call_args_list]
    # business_id is the authorization boundary in the select.
    assert any("where rr.business_id = %s" in s for s in sqls)
    # the delete counts actually-removed rows via `returning id`.
    assert any(
        "delete from public.research_sources" in s and "returning id" in s
        for s in sqls
    )
    client.delete_source.assert_awaited_once_with(SID)


async def test_bulk_delete_keeps_shared_source_deletes_only_orphan(monkeypatch):
    """Headline guarantee: a Source still referenced elsewhere (ref-count > 0) is NOT
    deleted from Powabase; only the orphaned one (count 0) is. A regression dropping the
    `== 0` check would delete project-wide Sources other brands still use."""
    db = MagicMock()
    shared, orphan = "src-shared", "src-orphan"
    db.fetch_all.return_value = [
        {"id": UUID(RID), "source_id": shared},
        {"id": UUID(RID2), "source_id": orphan},
    ]
    counts = {shared: 1, orphan: 0}
    monkeypatch.setattr(
        svc.source_refs, "source_reference_count", lambda d, s: counts[s]
    )
    client = MagicMock()
    client.delete_source = AsyncMock()
    await svc.bulk_delete_brand_sources(client, db, UUID(BID), [UUID(RID), UUID(RID2)])
    client.delete_source.assert_awaited_once_with(orphan)


async def test_bulk_delete_swallows_remote_delete_failure(monkeypatch):
    """Local rows are already deleted+committed — a remote delete_source hiccup must not
    convert the successful delete into a 500 (would strand the UI showing gone rows)."""
    db = MagicMock()
    db.fetch_all.return_value = [{"id": UUID(RID), "source_id": SID}]
    monkeypatch.setattr(svc.source_refs, "source_reference_count", lambda d, s: 0)
    client = MagicMock()
    client.delete_source = AsyncMock(side_effect=RuntimeError("remote down"))
    n = await svc.bulk_delete_brand_sources(client, db, UUID(BID), [UUID(RID)])
    assert n == 1  # returned normally despite the cleanup failure


async def test_bulk_delete_swallows_refcount_failure(monkeypatch):
    """Same guarantee for a failing ref-count query (pool timeout) in the cleanup loop."""
    db = MagicMock()
    db.fetch_all.return_value = [{"id": UUID(RID), "source_id": SID}]

    def boom(d, s):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(svc.source_refs, "source_reference_count", boom)
    client = MagicMock()
    client.delete_source = AsyncMock()
    n = await svc.bulk_delete_brand_sources(client, db, UUID(BID), [UUID(RID)])
    assert n == 1
    client.delete_source.assert_not_awaited()


# --- retry ---
def test_retry_requires_editor():
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        "/api/sources/retry", json={"business_id": BID, "row_ids": [RID]}
    )
    assert resp.status_code == 403


def test_retry_cross_org_404():
    # An editor in a DIFFERENT org gets 404 (not 403) so brand ids can't be probed across
    # orgs — assert_brand_access is the tenant-isolation boundary.
    other = CurrentUser(
        id=BID, role="admin", org_id="99999999-9999-9999-9999-999999999999"
    )
    resp = _client(user=other).post(
        "/api/sources/retry", json={"business_id": BID, "row_ids": [RID]}
    )
    assert resp.status_code == 404


def test_retry_spawns_worker_with_claimed_rows(monkeypatch):
    # The worker must receive the CLAIMED rows (from mark_sources_retrying), not the
    # request's raw row_ids — the claim is what filters/authorizes them.
    claimed = [{"id": RID2, "source_id": "s", "url": "u", "title": "t"}]
    monkeypatch.setattr(svc, "mark_sources_retrying", lambda db, bid, ids: claimed)
    worker = MagicMock(return_value=None)
    monkeypatch.setattr(svc, "retry_brand_sources", worker)
    monkeypatch.setattr("rankforge_backend.routes.sources.spawn", lambda coro: None)
    resp = _client().post(
        "/api/sources/retry", json={"business_id": BID, "row_ids": [RID, RID2]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 1}
    _, _, _, rows_arg = worker.call_args.args
    assert rows_arg == claimed


def test_retry_reverts_claim_when_spawn_fails(monkeypatch):
    # The claim commits before spawn; if the worker can't be scheduled, the claim must be
    # reverted so rows aren't stranded in 'retrying' with nothing behind them.
    claimed = [{"id": RID, "source_id": "old", "url": "https://x.com/a", "title": "A"}]
    monkeypatch.setattr(svc, "mark_sources_retrying", lambda db, bid, ids: claimed)
    monkeypatch.setattr(svc, "retry_brand_sources", AsyncMock())
    reverted = []
    monkeypatch.setattr(
        svc, "reset_sources_to_failed", lambda db, ids: reverted.append(ids)
    )

    def boom_spawn(coro):
        coro.close()  # we captured intent; don't leave an un-awaited coroutine
        raise RuntimeError("no task slot")

    monkeypatch.setattr("rankforge_backend.routes.sources.spawn", boom_spawn)
    with pytest.raises(RuntimeError):
        _client().post(
            "/api/sources/retry", json={"business_id": BID, "row_ids": [RID]}
        )
    assert reverted == [[RID]]  # the claim was reverted before the error propagated


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


def test_source_meta_502_on_upstream_error(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source = AsyncMock(side_effect=PowabaseError(500, "boom"))
    resp = _client(pb=pb).get(f"/api/sources/{SID}/meta")
    assert resp.status_code == 502


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


def test_source_page_image_404_when_upstream_404(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source_derivative_image = AsyncMock(side_effect=PowabaseError(404, "nope"))
    resp = _client(pb=pb).get(f"/api/sources/{SID}/pages/0")
    assert resp.status_code == 404


def test_source_page_image_502_when_upstream_errors(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source_derivative_image = AsyncMock(side_effect=PowabaseError(500, "boom"))
    resp = _client(pb=pb).get(f"/api/sources/{SID}/pages/0")
    assert resp.status_code == 502


def test_source_page_image_rejects_negative_index(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    resp = _client().get(f"/api/sources/{SID}/pages/-1")
    assert resp.status_code == 422


# --- markdown (the /markdown proxy: failed/extracting sources have no derivative) ---
def test_source_markdown_ok(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source_markdown = AsyncMock(return_value="# Title\n\nreal content")
    resp = _client(pb=pb).get(f"/api/sources/{SID}/markdown")
    assert resp.status_code == 200
    assert resp.json() == {"source_id": SID, "markdown": "# Title\n\nreal content"}


def test_source_markdown_empty_when_no_derivative(monkeypatch):
    """A failed/extracting scrape has no markdown derivative — Powabase 404s. That's a
    normal empty state, so the route returns empty markdown (200), NOT a 502."""
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source_markdown = AsyncMock(
        side_effect=PowabaseError(404, '{"error":"No markdown derivative found"}')
    )
    resp = _client(pb=pb).get(f"/api/sources/{SID}/markdown")
    assert resp.status_code == 200
    assert resp.json() == {"source_id": SID, "markdown": ""}


def test_source_markdown_502_on_upstream_fault(monkeypatch):
    """A genuine upstream error (5xx) is still a gateway fault → 502, not an empty body."""
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: True)
    pb = MagicMock()
    pb.get_source_markdown = AsyncMock(side_effect=PowabaseError(500, "boom"))
    resp = _client(pb=pb).get(f"/api/sources/{SID}/markdown")
    assert resp.status_code == 502


def test_source_markdown_404_cross_org(monkeypatch):
    monkeypatch.setattr(svc, "source_in_org", lambda db, sid, org: False)
    resp = _client().get(f"/api/sources/{SID}/markdown")
    assert resp.status_code == 404
