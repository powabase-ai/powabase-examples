"""Content scouts — scoring (unit) + route wiring (hermetic)."""

from unittest.mock import MagicMock

from conftest import with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import scouts as svc

BID = "11111111-1111-1111-1111-111111111111"
OID = "77777777-7777-7777-7777-777777777777"

BRAND_TERMS = svc._brand_terms(
    {
        "niche": "headless cms",
        "seed_topics": ["jamstack", "static sites"],
        "target_keywords": ["headless cms"],
    }
)

CFG = {
    "business_id": BID,
    "enabled": False,
    "cadence": "daily",
    "autonomy": "suggest",
    "min_score": 70,
    "max_drafts_per_run": 1,
    "focus": [],
    "last_run_at": None,
    "next_run_at": None,
    "updated_at": "2026-06-20T00:00:00Z",
}
OPP = {
    "id": OID,
    "business_id": BID,
    "scout_run_id": None,
    "title": "X",
    "angle": None,
    "why_now": None,
    "keyword": "x",
    "source_type": "news",
    "source_url": None,
    "evidence": {},
    "score": 80,
    "scores": {},
    "status": "new",
    "article_id": None,
    "created_at": "2026-06-20T00:00:00Z",
    "updated_at": "2026-06-20T00:00:00Z",
}


# --- scoring (unit) ---
def test_score_candidate_rewards_relevance():
    on_brand = {
        "title": "Best headless CMS for jamstack sites",
        "keyword": "headless cms",
        "opportunity_score": 80,
    }
    off_brand = {
        "title": "Cooking pasta at home",
        "keyword": "pasta",
        "opportunity_score": 80,
    }
    s_on, b_on = svc.score_candidate(on_brand, BRAND_TERMS)
    s_off, _ = svc.score_candidate(off_brand, BRAND_TERMS)
    assert s_on > s_off
    assert b_on["overlap_terms"] >= 1


def test_score_clamps_missing_agent_score():
    s, b = svc.score_candidate({"title": "x", "keyword": "y"}, BRAND_TERMS)
    assert 0 <= s <= 100
    assert b["agent_score"] == 50


def test_norm_title_dedups():
    assert svc._norm_title("Headless CMS, Compared!") == svc._norm_title(
        "headless cms compared"
    )


# --- routes (hermetic) ---
def _client(db, user: CurrentUser | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    with_auth(app, user) if user else with_auth(app)
    return TestClient(app)


def test_get_config_ensures(monkeypatch):
    monkeypatch.setattr(svc, "ensure_config", lambda db, bid: CFG)
    resp = _client(MagicMock()).get(f"/api/scouts/config?business_id={BID}")
    assert resp.status_code == 200
    assert resp.json()["cadence"] == "daily"


def test_update_config_requires_editor(monkeypatch):
    monkeypatch.setattr(svc, "update_config", lambda db, bid, f: {**CFG, "enabled": True})
    writer = CurrentUser(id=BID, role="writer")
    resp = _client(MagicMock(), writer).put(
        f"/api/scouts/config?business_id={BID}", json={"enabled": True}
    )
    assert resp.status_code == 403


def test_update_config_editor_ok(monkeypatch):
    monkeypatch.setattr(svc, "update_config", lambda db, bid, f: {**CFG, "enabled": True})
    resp = _client(MagicMock()).put(
        f"/api/scouts/config?business_id={BID}", json={"enabled": True}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_run_now_202(monkeypatch):
    async def fake_run(*a, **k):
        return None

    monkeypatch.setattr(svc, "run_scout", fake_run)
    resp = _client(MagicMock()).post(f"/api/scouts/run?business_id={BID}")
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"


def test_list_opportunities(monkeypatch):
    monkeypatch.setattr(svc, "list_opportunities", lambda db, bid: [OPP])
    resp = _client(MagicMock()).get(f"/api/opportunities?business_id={BID}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_dismiss(monkeypatch):
    monkeypatch.setattr(
        svc, "set_opportunity_status", lambda db, oid, st, **k: {**OPP, "status": st}
    )
    resp = _client(MagicMock()).post(f"/api/opportunities/{OID}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"


def test_draft_spawns(monkeypatch):
    monkeypatch.setattr(svc, "get_opportunity", lambda db, oid: OPP)
    monkeypatch.setattr(
        svc, "set_opportunity_status", lambda db, oid, st, **k: {**OPP, "status": st}
    )

    async def fake_draft(*a, **k):
        return True

    monkeypatch.setattr(svc, "auto_draft", fake_draft)
    resp = _client(MagicMock()).post(f"/api/opportunities/{OID}/draft")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
