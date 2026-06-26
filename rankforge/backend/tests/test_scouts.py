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
RID = "99999999-9999-9999-9999-999999999999"
PLANNED_RUN = {
    "id": RID, "business_id": BID, "status": "planned", "trigger": "manual",
    "found": 0, "drafted": 0, "error": None,
    "progress": {"phase": "planning", "message": "…"}, "plan": None,
    "created_at": "2026-06-20T00:00:00Z",
}

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


def test_pillar_subtopics_filters_generic_and_dedups():
    pillar = {"content_md": "## Introduction\n\n## SSO Setup\n\n## SSO Setup\n\n## FAQ\n"}
    out = svc._pillar_subtopics(pillar, {"secondary_keywords": ["mfa setup"]})
    labels = [s["label"] for s in out]
    assert "mfa setup" in labels  # from the brief
    assert "SSO Setup" in labels  # an H2
    assert "Introduction" not in labels and "FAQ" not in labels  # generic dropped
    assert labels.count("SSO Setup") == 1  # deduped


def test_analyze_cluster_gaps_stages_opps_for_uncovered_subtopics(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        svc.clusters, "get_cluster",
        lambda d, cid: {"label": "Auth", "pillar_article_id": "P"},
    )
    monkeypatch.setattr(
        svc.generation, "get_article",
        lambda d, aid: {"id": "P", "brief_id": "B",
                        "content_md": "## SSO Setup\n\n## MFA Setup\n\n## Conclusion\n"},
    )
    monkeypatch.setattr(svc.brief_svc, "get_brief", lambda d, bid: {"secondary_keywords": []})
    monkeypatch.setattr(
        svc, "_gather_coverage",
        lambda d, bid: {"seen": set(), "keywords": set(), "token_sets": [],
                        "articles": [], "opps": []},
    )
    n = svc.analyze_cluster_gaps(db, BID, "C")
    assert n == 2  # SSO Setup + MFA Setup (Conclusion filtered out)
    q = db.execute.call_args.args[0]
    assert "insert into public.opportunities" in q and "'gap'" in q


def test_analyze_cluster_gaps_zero_without_a_pillar(monkeypatch):
    monkeypatch.setattr(svc.clusters, "get_cluster",
                        lambda d, cid: {"label": "Auth", "pillar_article_id": None})
    assert svc.analyze_cluster_gaps(MagicMock(), BID, "C") == 0


def test_analyze_all_gaps_respects_the_budget(monkeypatch):
    monkeypatch.setattr(
        svc.clusters, "list_clusters",
        lambda d, bid: [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}],
    )
    monkeypatch.setattr(svc, "analyze_cluster_gaps", lambda d, bid, cid: 15)
    total = svc.analyze_all_gaps(MagicMock(), BID, budget=20)
    assert total == 30  # c1 (15) + c2 (30 ≥ 20) → stop; c3 never runs


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


def test_delete_opportunity_route(monkeypatch):
    monkeypatch.setattr(svc, "get_opportunity", lambda d, oid: OPP)
    removed = MagicMock(return_value=True)
    monkeypatch.setattr(svc, "delete_opportunity", removed)
    resp = _client().delete(f"/api/opportunities/{OID}")
    assert resp.status_code == 204
    removed.assert_called_once()


def test_delete_opportunity_requires_editor(monkeypatch):
    monkeypatch.setattr(svc, "get_opportunity", lambda d, oid: OPP)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).delete(f"/api/opportunities/{OID}")
    assert resp.status_code == 403


def test_delete_opportunity_blocked_while_drafting(monkeypatch):
    """Deleting an opportunity mid-draft would orphan the article being created."""
    monkeypatch.setattr(
        svc, "get_opportunity", lambda d, oid: {**OPP, "status": "drafting"}
    )
    removed = MagicMock(return_value=True)
    monkeypatch.setattr(svc, "delete_opportunity", removed)
    resp = _client().delete(f"/api/opportunities/{OID}")
    assert resp.status_code == 409
    removed.assert_not_called()


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


async def test_auto_draft_refuses_when_already_claimed(monkeypatch):
    """The CAS claim is the single gate: if another drafter already has it (claim
    returns None), auto_draft bails before spending any research/LLM budget."""
    db = MagicMock()
    db.fetch_one.return_value = None  # compare-and-set lost → already drafting/drafted
    called = False

    def _boom(*a, **k):
        nonlocal called
        called = True
        return {"id": "r1"}

    monkeypatch.setattr(svc.research_svc, "create_research_run", _boom)
    ok = await svc.auto_draft(
        MagicMock(), db,
        {"id": "o1", "business_id": BID, "keyword": "k", "title": "t"},
    )
    assert ok is False
    assert called is False  # never started the pipeline


def test_run_now_202(monkeypatch):
    async def fake_run(*a, **k):
        return None

    monkeypatch.setattr(svc, "run_scout", fake_run)
    resp = _client().post(f"/api/scouts/run?business_id={BID}")
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"


# --- two-phase plan → edit → execute ---
def test_normalize_plan_sanitizes():
    out = svc._normalize_plan({
        "themes": ["a", "   ", "b"],
        "queries": [
            {"query": "  hot topic ", "source": "news", "rationale": "fresh"},
            {"query": "", "source": "web"},          # dropped (empty)
            {"query": "vid", "source": "bogus"},      # invalid source → web
            "not a dict",                              # ignored
        ],
        "edited": True,
    })
    assert out["themes"] == ["a", "b"]
    assert [q["query"] for q in out["queries"]] == ["hot topic", "vid"]
    assert out["queries"][0]["source"] == "news"
    assert out["queries"][1]["source"] == "web"
    assert out["edited"] is True


def test_update_plan_guards_on_planned_status():
    db = MagicMock()
    db.fetch_one.return_value = {**PLANNED_RUN}
    out = svc.update_plan(db, RID, {"queries": [{"query": "q", "source": "web"}]})
    assert out is not None
    sql = db.fetch_one.call_args.args[0].lower()
    assert "status = 'planned'" in sql  # only editable before it runs


def test_plan_route_returns_planned_run(monkeypatch):
    monkeypatch.setattr(svc, "start_plan", lambda db, bid, **k: PLANNED_RUN)

    async def fake_gen(*a, **k):
        return None

    monkeypatch.setattr(svc, "generate_plan_for_run", fake_gen)
    resp = _client().post(f"/api/scouts/plan?business_id={BID}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "planned"


def test_plan_route_requires_editor():
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(f"/api/scouts/plan?business_id={BID}")
    assert resp.status_code == 403


def test_update_plan_route_editor(monkeypatch):
    monkeypatch.setattr(svc, "get_run", lambda db, rid: {**PLANNED_RUN})
    monkeypatch.setattr(
        svc, "update_plan",
        lambda db, rid, plan: {**PLANNED_RUN, "plan": {**plan, "edited": True}},
    )
    resp = _client().patch(
        f"/api/scouts/runs/{RID}/plan",
        json={"themes": [], "queries": [{"query": "x", "source": "news"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["plan"]["edited"] is True


def test_update_plan_route_409_when_started(monkeypatch):
    monkeypatch.setattr(svc, "get_run", lambda db, rid: {**PLANNED_RUN})
    monkeypatch.setattr(svc, "update_plan", lambda db, rid, plan: None)  # already running
    resp = _client().patch(f"/api/scouts/runs/{RID}/plan", json={"queries": []})
    assert resp.status_code == 409


def test_execute_route_202_when_planned(monkeypatch):
    monkeypatch.setattr(svc, "get_run", lambda db, rid: {**PLANNED_RUN})

    async def fake_exec(*a, **k):
        return None

    monkeypatch.setattr(svc, "execute_run", fake_exec)
    resp = _client().post(f"/api/scouts/runs/{RID}/execute")
    assert resp.status_code == 202


def test_execute_route_409_when_not_planned(monkeypatch):
    monkeypatch.setattr(svc, "get_run", lambda db, rid: {**PLANNED_RUN, "status": "done"})
    resp = _client().post(f"/api/scouts/runs/{RID}/execute")
    assert resp.status_code == 409


def test_start_plan_clears_prior_planned():
    db = MagicMock()
    db.fetch_one.return_value = PLANNED_RUN  # ensure_config existing + insert returning
    svc.start_plan(db, BID)
    sqls = " ".join(c.args[0].lower() for c in db.execute.call_args_list)
    assert "delete from public.scout_runs" in sqls and "status = 'planned'" in sqls


async def test_execute_run_claims_atomically(monkeypatch):
    """A lost compare-and-set (someone already started the run) must NOT re-discover."""
    db = MagicMock()
    monkeypatch.setattr(
        svc, "get_run", lambda d, rid: {**PLANNED_RUN, "plan": {"queries": []}}
    )
    monkeypatch.setattr(svc, "ensure_config", lambda d, b: CFG)
    monkeypatch.setattr(svc.brands, "get_profile", lambda d, b: {"name": "X"})
    db.fetch_one.return_value = None  # claim lost (status no longer 'planned')
    discovered: list[int] = []

    async def fake_disc(*a, **k):
        discovered.append(1)

    monkeypatch.setattr(svc, "_discover_and_store", fake_disc)
    await svc.execute_run(MagicMock(), db, RID)
    assert discovered == []  # didn't win the claim → no duplicate run
    claim_sql = db.fetch_one.call_args.args[0].lower()
    assert "status = 'running'" in claim_sql and "status = 'planned'" in claim_sql


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


def test_restore_undismisses(monkeypatch):
    monkeypatch.setattr(
        svc, "get_opportunity", lambda db, oid: {**OPP, "status": "dismissed"}
    )
    monkeypatch.setattr(
        svc, "set_opportunity_status", lambda db, oid, st, **k: {**OPP, "status": st}
    )
    resp = _client().post(f"/api/opportunities/{OID}/restore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "new"


def test_restore_noop_when_not_dismissed(monkeypatch):
    monkeypatch.setattr(svc, "get_opportunity", lambda db, oid: {**OPP, "status": "new"})
    called: list[str] = []
    monkeypatch.setattr(
        svc,
        "set_opportunity_status",
        lambda db, oid, st, **k: called.append(st) or {**OPP, "status": st},
    )
    resp = _client().post(f"/api/opportunities/{OID}/restore")
    assert resp.status_code == 200
    assert called == []  # a non-dismissed opp is returned unchanged


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
