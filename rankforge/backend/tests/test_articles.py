"""Articles — section parsing (unit) + async route wiring (hermetic)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import generation as svc

BRIEF_ID = "44444444-4444-4444-4444-444444444444"
BID = "11111111-1111-1111-1111-111111111111"
ARTICLE = {
    "id": "55555555-5555-5555-5555-555555555555",
    "business_id": BID,
    "brief_id": BRIEF_ID,
    "research_run_id": None,
    "title": "Headless CMS Comparison",
    "slug": "headless-cms-comparison",
    "status": "draft",
    "generation_status": "grounding",
    "generation_error": None,
    "progress": {},
    "content_md": "",
    "meta_title": None,
    "meta_description": None,
    "seo_score": None,
    "geo_score": None,
    "created_at": "2026-06-19T00:00:00Z",
    "updated_at": "2026-06-19T00:00:00Z",
}


def test_parse_sections_groups_h3_under_h2():
    secs = svc.parse_sections(["H2: A", "H3: a1", "H3: a2", "H2: B"])
    assert len(secs) == 2
    assert secs[0] == {"h2": "A", "subs": ["a1", "a2"]}
    assert secs[1] == {"h2": "B", "subs": []}


def test_parse_sections_handles_unprefixed():
    secs = svc.parse_sections(["Intro", "Details"])
    assert [s["h2"] for s in secs] == ["Intro", "Details"]


def test_outline_text_marks_sections_and_subsections():
    out = svc._outline_text(["H2: Overview", "H3: Details", "H2: Pricing"])
    assert "Overview  (## section)" in out
    assert "Details  (### subsection)" in out
    assert "Pricing  (## section)" in out


async def test_gather_grounding_gives_each_query_a_fair_share(monkeypatch):
    """Every query (incl. section-heading queries) must contribute — a broad early
    query must NOT fill the budget and starve later sections of grounding."""
    from collections import Counter

    async def fake_search(client, kb, q, **kw):
        return [
            {"chunk_id": f"{q}-{i}", "source_id": f"{q}-s{i}", "text": q}
            for i in range(10)
        ]

    monkeypatch.setattr(svc.grounding, "search", fake_search)
    out = await svc._gather_grounding(
        MagicMock(), "kb", ["A", "B", "C"], per_query=2
    )
    cnt = Counter(c["text"] for c in out)
    assert set(cnt) == {"A", "B", "C"}  # no query starved
    assert all(v <= 2 for v in cnt.values())  # per_query respected


async def test_gather_grounding_empty_without_kb_or_queries():
    assert await svc._gather_grounding(MagicMock(), None, ["q"]) == []
    assert await svc._gather_grounding(MagicMock(), "kb", [None, ""]) == []


async def test_ensure_writer_agent_passes_system_prompt_and_budget(monkeypatch):
    """Regression: the writer agent must carry its system prompt (and a raised
    max_tokens for whole-article output) — dropping either breaks all generation."""
    captured = {}

    async def fake_ensure_agent(client, **kwargs):
        captured.update(kwargs)
        return "writer-id"

    monkeypatch.setattr(svc, "ensure_agent", fake_ensure_agent)
    assert await svc.ensure_writer_agent(MagicMock()) == "writer-id"
    assert captured.get("system_prompt")  # the bug that slipped through
    assert captured["settings"]["max_tokens"] == 32000


def _brand_db() -> MagicMock:
    """A db mock whose fetch_one satisfies assert_brand_access (org match)."""
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def make_client(db: MagicMock | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db or _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app))


def test_refine_starts_when_claim_succeeds(monkeypatch):
    from rankforge_backend.routes import articles as articles_route

    monkeypatch.setattr(svc, "get_article", lambda db, aid: ARTICLE)
    monkeypatch.setattr(svc, "try_begin_refine", lambda db, aid, total: True)

    async def fake_finish(*a, **k):
        return None

    monkeypatch.setattr(articles_route, "_refine_and_finish", fake_finish)
    resp = make_client().post(f"/api/articles/{ARTICLE['id']}/refine")
    assert resp.status_code == 200


def test_refine_conflict_when_already_in_progress(monkeypatch):
    """A second refine while one is in flight must 409, not launch a 2nd pipeline."""
    monkeypatch.setattr(svc, "get_article", lambda db, aid: ARTICLE)
    monkeypatch.setattr(svc, "try_begin_refine", lambda db, aid, total: False)
    resp = make_client().post(f"/api/articles/{ARTICLE['id']}/refine")
    assert resp.status_code == 409


def test_retry_starts_when_claim_succeeds(monkeypatch):
    async def fake_task(*a, **k):
        return None

    monkeypatch.setattr(svc, "get_article", lambda db, aid: ARTICLE)
    monkeypatch.setattr(svc, "get_brief", lambda db, bid: {"id": BRIEF_ID})
    monkeypatch.setattr(svc, "try_begin_generation", lambda db, aid: True)
    monkeypatch.setattr(svc, "run_generation_task", fake_task)
    resp = make_client().post(f"/api/articles/{ARTICLE['id']}/retry")
    assert resp.status_code == 200


def test_retry_conflict_when_already_in_progress(monkeypatch):
    monkeypatch.setattr(svc, "get_article", lambda db, aid: ARTICLE)
    monkeypatch.setattr(svc, "get_brief", lambda db, bid: {"id": BRIEF_ID})
    monkeypatch.setattr(svc, "try_begin_generation", lambda db, aid: False)
    resp = make_client().post(f"/api/articles/{ARTICLE['id']}/retry")
    assert resp.status_code == 409


def test_retry_409_when_no_brief(monkeypatch):
    """An article whose brief is gone can't be regenerated — 409, not a 500."""
    no_brief = {**ARTICLE, "brief_id": None}
    monkeypatch.setattr(svc, "get_article", lambda db, aid: no_brief)
    resp = make_client().post(f"/api/articles/{ARTICLE['id']}/retry")
    assert resp.status_code == 409


def test_generate_article_201(monkeypatch):
    async def fake_task(*args, **kwargs):
        return None

    monkeypatch.setattr(svc, "get_brief", lambda db, bid: {"id": BRIEF_ID, "business_id": BID})
    monkeypatch.setattr(
        svc, "create_article", lambda db, brief, author_id=None: ARTICLE
    )
    monkeypatch.setattr(svc, "run_generation_task", fake_task)

    resp = make_client().post("/api/articles", json={"brief_id": BRIEF_ID})
    assert resp.status_code == 201
    assert resp.json()["generation_status"] == "grounding"


def test_generate_article_unknown_brief_404(monkeypatch):
    monkeypatch.setattr(svc, "get_brief", lambda db, bid: None)
    resp = make_client().post("/api/articles", json={"brief_id": BRIEF_ID})
    assert resp.status_code == 404


def test_delete_article_204(monkeypatch):
    monkeypatch.setattr(svc, "get_article", lambda db, aid: ARTICLE)
    removed = MagicMock(return_value=True)
    monkeypatch.setattr(svc, "delete_article", removed)
    resp = make_client().delete(f"/api/articles/{ARTICLE['id']}")
    assert resp.status_code == 204
    removed.assert_called_once()


def test_delete_article_requires_editor(monkeypatch):
    monkeypatch.setattr(svc, "get_article", lambda db, aid: ARTICLE)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    resp = TestClient(with_auth(app, writer)).delete(f"/api/articles/{ARTICLE['id']}")
    assert resp.status_code == 403


def test_delete_article_recovers_drafted_opportunities():
    """A 'drafted' opportunity tied to the article is returned to the inbox before the
    row is deleted (one transaction), so it isn't left stranded with a dead link."""
    db = MagicMock()
    db.fetch_all.return_value = []  # no other articles cite this one
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"id": ARTICLE["id"]}
    db.connection.return_value.__enter__.return_value = conn
    assert svc.delete_article(db, ARTICLE["id"]) is True
    opp_sql = conn.execute.call_args_list[0].args[0].lower()
    assert "update public.opportunities" in opp_sql
    assert "status = 'new'" in opp_sql
    assert "where article_id = %s and status = 'drafted'" in opp_sql
    del_sql = conn.execute.call_args_list[1].args[0].lower()
    assert "delete from public.articles" in del_sql


def test_update_article_patches():
    db = MagicMock()
    db.fetch_one.return_value = {**ARTICLE, "title": "Edited", "org_id": UUID(ADMIN_ORG)}
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    resp = TestClient(with_auth(app)).patch(
        f"/api/articles/{ARTICLE['id']}", json={"title": "Edited"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Edited"
    sql = db.fetch_one.call_args.args[0].lower()
    assert "update public.articles" in sql


# --- auto link-check after refine (broken/fabricated URLs surface without a manual run) ---
async def test_refine_and_finish_revalidates_links_before_done(monkeypatch):
    from rankforge_backend.routes import articles as art_routes

    monkeypatch.setattr(art_routes.revise_svc, "refine", AsyncMock())
    monkeypatch.setattr(
        art_routes.svc, "get_article",
        lambda db, aid: {"id": aid, "business_id": BID, "content_md": "the body"},
    )
    monkeypatch.setattr(art_routes.svc, "_update", lambda *a, **k: None)
    chk = AsyncMock(return_value=[])
    monkeypatch.setattr(art_routes.linkcheck_svc, "check_article", chk)

    await art_routes._refine_and_finish(MagicMock(), MagicMock(), UUID(ARTICLE["id"]))
    chk.assert_awaited_once()


async def test_refine_and_finish_skips_link_check_when_no_content(monkeypatch):
    # An empty/failed refine → terminal 'failed' → don't waste a link check.
    from rankforge_backend.routes import articles as art_routes

    monkeypatch.setattr(art_routes.revise_svc, "refine", AsyncMock())
    monkeypatch.setattr(
        art_routes.svc, "get_article",
        lambda db, aid: {"id": aid, "business_id": BID, "content_md": ""},
    )
    monkeypatch.setattr(art_routes.svc, "_update", lambda *a, **k: None)
    chk = AsyncMock()
    monkeypatch.setattr(art_routes.linkcheck_svc, "check_article", chk)

    await art_routes._refine_and_finish(MagicMock(), MagicMock(), UUID(ARTICLE["id"]))
    chk.assert_not_awaited()
