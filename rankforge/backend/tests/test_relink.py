"""Monthly re-linking scout (M6 / Phase 12.3) — service + route wiring."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db
from rankforge_backend.services import relink

BID = "11111111-1111-1111-1111-111111111111"
A1 = "22222222-2222-2222-2222-222222222222"
A2 = "33333333-3333-3333-3333-333333333333"


# --- service ---
def test_run_relink_scans_library_and_reschedules(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {"business_id": BID, "cadence": "monthly"}  # ensure_config
    db.fetch_all.return_value = [{"id": A1}, {"id": A2}]  # two published articles
    # each article yields one new suggestion
    monkeypatch.setattr(
        relink.linking, "suggest_links",
        lambda d, bid, aid, candidates=None: [{"id": "x"}],
    )
    out = relink.run_relink(db, BID)
    assert out == {"articles_scanned": 2, "suggestions_found": 2}
    queries = [c[0][0] for c in db.execute.call_args_list]
    assert any("last_found" in q for q in queries)  # recorded the count
    assert any("next_run_at" in q for q in queries)  # rolled the schedule forward


def test_run_relink_reschedules_even_if_an_article_fails(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {"business_id": BID, "cadence": "monthly"}
    db.fetch_all.return_value = [{"id": A1}, {"id": A2}]

    def boom(d, bid, aid, candidates=None):
        if aid == A1:
            raise RuntimeError("nope")
        return [{"id": "y"}]

    monkeypatch.setattr(relink.linking, "suggest_links", boom)
    out = relink.run_relink(db, BID)
    assert out == {"articles_scanned": 2, "suggestions_found": 1}  # A1 failed, A2 ok
    assert any("next_run_at" in c[0][0] for c in db.execute.call_args_list)


def test_update_config_sets_next_run_when_enabling():
    db = MagicMock()
    db.fetch_one.return_value = {"business_id": BID, "cadence": "monthly"}
    relink.update_config(db, BID, {"enabled": True})
    q = db.fetch_one.call_args[0][0]  # the final UPDATE ... returning
    assert "update public.relink_configs" in q
    assert "next_run_at" in q


def test_due_configs_filters_enabled_and_due():
    db = MagicMock()
    relink.due_configs(db)
    q = db.fetch_all.call_args[0][0]
    assert "where enabled" in q
    assert "next_run_at is null or next_run_at <= now()" in q


# --- routes (hermetic) ---
def _brand_db() -> MagicMock:
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db=None, user: CurrentUser | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    return TestClient(with_auth(app, user) if user else with_auth(app))


def test_get_relink_config_defaults(monkeypatch):
    monkeypatch.setattr(relink, "get_config", lambda d, bid: None)
    resp = _client().get(f"/api/business-profiles/{BID}/relink")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["cadence"] == "monthly"


def test_put_relink_requires_editor():
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).put(
        f"/api/business-profiles/{BID}/relink", json={"enabled": True}
    )
    assert resp.status_code == 403


def test_put_relink_updates(monkeypatch):
    monkeypatch.setattr(
        relink, "update_config",
        lambda d, bid, f: {
            "business_id": str(BID), "enabled": True, "cadence": "monthly",
            "last_found": 0,
        },
    )
    resp = _client().put(
        f"/api/business-profiles/{BID}/relink", json={"enabled": True}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_run_relink_now_is_202(monkeypatch):
    monkeypatch.setattr(
        relink, "run_relink",
        lambda d, bid: {"articles_scanned": 0, "suggestions_found": 0},
    )
    resp = _client().post(f"/api/business-profiles/{BID}/relink/run")
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"
