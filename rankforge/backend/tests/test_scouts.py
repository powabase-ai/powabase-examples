"""Content scouts — scoring (unit) + route wiring (hermetic)."""

from unittest.mock import MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
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


def test_cadence_delta():
    from datetime import timedelta

    assert svc._cadence_delta("twice_daily") == timedelta(hours=12)
    assert svc._cadence_delta("daily") == timedelta(days=1)
    assert svc._cadence_delta("weekly") == timedelta(days=7)
    assert svc._cadence_delta("bogus") == timedelta(days=1)  # safe default


def test_gather_coverage_includes_articles_and_dismissed():
    db = MagicMock()
    db.fetch_all.side_effect = [
        [{"title": "My Article", "keywords": ["my kw"], "slug": "my-article"}],
        [{"title": "Open Opp", "keyword": "open kw"}],
        [{"title": "Dismissed Topic"}],
    ]
    cov = svc._gather_coverage(db, "11111111-1111-1111-1111-111111111111")
    assert svc._norm_title("My Article") in cov["seen"]
    # dismissed topics are folded in so they don't keep resurfacing
    assert svc._norm_title("Dismissed Topic") in cov["seen"]
    assert svc._norm_title("my kw") in cov["keywords"]
    assert svc._norm_title("my article") in cov["keywords"]  # from the slug


def test_covers_existing_catches_dups_but_allows_new():
    cov = {
        "seen": {svc._norm_title("Best Headless CMS for Startups")},
        "keywords": {svc._norm_title("headless cms")},
        "token_sets": [svc._tokens("Best Headless CMS for Startups")],
    }
    # exact (normalized) title
    assert svc._covers_existing("best headless cms for startups!", None, cov)
    # same primary keyword, different title
    assert svc._covers_existing("A New Spin", "Headless CMS", cov)
    # reworded near-duplicate (high title-token overlap)
    assert svc._covers_existing("Best Headless CMS for Startups in 2026", None, cov)
    # genuinely new topic + keyword passes
    assert not svc._covers_existing(
        "Edge Caching Strategies for APIs", "edge caching", cov
    )


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


def test_get_config_returns_existing(monkeypatch):
    monkeypatch.setattr(svc, "get_config", lambda db, bid: CFG)
    resp = _client().get(f"/api/scouts/config?business_id={BID}")
    assert resp.status_code == 200
    assert resp.json()["cadence"] == "daily"


def test_update_config_requires_editor(monkeypatch):
    monkeypatch.setattr(svc, "update_config", lambda db, bid, f: {**CFG, "enabled": True})
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).put(
        f"/api/scouts/config?business_id={BID}", json={"enabled": True}
    )
    assert resp.status_code == 403


def test_update_config_editor_ok(monkeypatch):
    monkeypatch.setattr(svc, "update_config", lambda db, bid, f: {**CFG, "enabled": True})
    resp = _client().put(
        f"/api/scouts/config?business_id={BID}", json={"enabled": True}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_update_config_rejects_out_of_range_drafts():
    # Pydantic bounds guard against a runaway autonomous-spend value.
    resp = _client().put(
        f"/api/scouts/config?business_id={BID}", json={"max_drafts_per_run": 999}
    )
    assert resp.status_code == 422


def test_get_config_does_not_persist(monkeypatch):
    # GET must be read-only — returns a default without inserting a row.
    monkeypatch.setattr(svc, "get_config", lambda db, bid: None)
    resp = _client().get(f"/api/scouts/config?business_id={BID}")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_auto_draft_bails_on_failed_research(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(svc.brands, "get_profile", lambda db, bid: {"id": BID})
    monkeypatch.setattr(
        svc.research_svc, "create_research_run", lambda db, **k: {"id": "r1"}
    )

    async def fake_task(*a, **k):
        return None

    monkeypatch.setattr(svc.research_svc, "run_research_task", fake_task)
    monkeypatch.setattr(svc.research_svc, "get_run", lambda db, rid: {"status": "failed"})
    monkeypatch.setattr(
        svc, "set_opportunity_status", lambda db, oid, st, **k: seen.append(st)
    )
    ok = await svc.auto_draft(
        MagicMock(), MagicMock(),
        {"id": "o1", "business_id": BID, "keyword": "k", "title": "t"},
    )
    assert ok is False
    assert seen[-1] == "new"


def test_run_now_202(monkeypatch):
    async def fake_run(*a, **k):
        return None

    monkeypatch.setattr(svc, "run_scout", fake_run)
    resp = _client().post(f"/api/scouts/run?business_id={BID}")
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"


def test_list_opportunities(monkeypatch):
    monkeypatch.setattr(svc, "list_opportunities", lambda db, bid: [OPP])
    resp = _client().get(f"/api/opportunities?business_id={BID}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_dismiss(monkeypatch):
    monkeypatch.setattr(svc, "get_opportunity", lambda db, oid: OPP)
    monkeypatch.setattr(
        svc, "set_opportunity_status", lambda db, oid, st, **k: {**OPP, "status": st}
    )
    resp = _client().post(f"/api/opportunities/{OID}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"


def test_draft_spawns(monkeypatch):
    monkeypatch.setattr(svc, "get_opportunity", lambda db, oid: OPP)
    # The route now claims the opportunity atomically (compare-and-set) before
    # spawning; a successful claim returns the queued row.
    monkeypatch.setattr(
        svc, "try_claim_opportunity", lambda db, oid: {**OPP, "status": "queued"}
    )

    async def fake_draft(*a, **k):
        return True

    monkeypatch.setattr(svc, "auto_draft", fake_draft)
    resp = _client().post(f"/api/opportunities/{OID}/draft")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_draft_already_in_progress_no_double_spawn(monkeypatch):
    """A second draft of an in-flight opportunity must not launch another pipeline."""
    monkeypatch.setattr(svc, "get_opportunity", lambda db, oid: {**OPP, "status": "drafting"})
    monkeypatch.setattr(svc, "try_claim_opportunity", lambda db, oid: None)

    spawned = []

    async def fake_draft(*a, **k):
        spawned.append(1)

    monkeypatch.setattr(svc, "auto_draft", fake_draft)
    resp = _client().post(f"/api/opportunities/{OID}/draft")
    assert resp.status_code == 200
    assert resp.json()["status"] == "drafting"
    assert spawned == []  # claim failed → no second pipeline
