"""LinkedIn post generator — models, prompt builder, CRUD service, routes (hermetic)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rankforge_backend.main import create_app
from rankforge_backend.models.linkedin import (
    ANGLE_SLUGS,
    LinkedInGenerate,
    LinkedInUpdate,
)
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.powabase import PowabaseError
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import generation as gen_svc
from rankforge_backend.services import linkedin_gen as li_gen
from rankforge_backend.services import linkedin_posts as li_svc


def test_angle_slugs_are_the_five_presets():
    assert ANGLE_SLUGS == ("key_insight", "lesson", "contrarian", "story", "stat")


def test_angle_clauses_cover_every_slug():
    # _ANGLE_CLAUSES is the fifth copy of the preset list — pin it to the source of truth.
    assert set(li_gen._ANGLE_CLAUSES) == set(ANGLE_SLUGS)


def test_generate_defaults_to_key_insight():
    assert LinkedInGenerate().angle == "key_insight"


def test_generate_rejects_unknown_angle():
    with pytest.raises(ValidationError):
        LinkedInGenerate(angle="spicy")


def test_update_rejects_empty_and_overlong_body():
    with pytest.raises(ValidationError):
        LinkedInUpdate(body="")
    with pytest.raises(ValidationError):
        LinkedInUpdate(body="x" * 3001)
    assert LinkedInUpdate(body="ok").body == "ok"


def test_update_strips_whitespace_and_rejects_blank():
    # A whitespace-only body is empty; the API must be as strict as the UI.
    with pytest.raises(ValidationError):
        LinkedInUpdate(body="   ")
    assert LinkedInUpdate(body="  ok  ").body == "ok"


AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"
PID = "66666666-6666-6666-6666-666666666666"


def test_create_post_inserts_with_angle_and_author():
    db = MagicMock()
    db.fetch_one.return_value = {"id": PID, "article_id": AID, "angle": "story", "body": "hi"}
    out = li_svc.create_post(
        db, article_id=AID, business_id=BID, angle="story", body="hi", author_id=AID
    )
    sql = db.fetch_one.call_args.args[0].lower()
    assert "insert into public.linkedin_posts" in sql
    assert db.fetch_one.call_args.args[1] == (AID, BID, "story", "hi", AID)
    assert out["angle"] == "story"


def test_list_posts_orders_newest_first():
    db = MagicMock()
    db.fetch_all.return_value = []
    li_svc.list_posts(db, AID)
    sql = db.fetch_all.call_args.args[0].lower()
    assert "where article_id = %s" in sql
    assert "order by created_at desc" in sql


def test_delete_post_returns_bool():
    db = MagicMock()
    db.fetch_one.return_value = {"id": PID}
    assert li_svc.delete_post(db, PID) is True
    db.fetch_one.return_value = None
    assert li_svc.delete_post(db, PID) is False


def test_build_prompt_includes_angle_clause_and_truncates():
    long_body = "word " * 5000  # ~25k chars
    msg = li_gen.build_linkedin_prompt(
        title="Governed BaaS",
        content_md=long_body,
        brand={"description": "We build a governed BaaS.", "niche": "devtools"},
        angle="contrarian",
        article_url=None,
    )
    assert "Governed BaaS" in msg
    assert li_gen._ANGLE_CLAUSES["contrarian"] in msg
    # content is truncated to 16k chars of the article body
    assert long_body[:16000] in msg
    assert len(long_body) > 16000 and long_body not in msg
    # no link instruction when there's no url
    assert "Full write-up" not in msg


def test_build_prompt_adds_link_line_when_url_present():
    msg = li_gen.build_linkedin_prompt(
        title="T", content_md="body", brand={}, angle="stat",
        article_url="https://blog.acme.com/governed-baas",
    )
    assert "https://blog.acme.com/governed-baas" in msg
    assert "Full write-up" in msg


async def test_generate_post_returns_agent_text(monkeypatch):
    article = {"id": AID, "business_id": BID, "title": "T",
               "content_md": "# T\n\nreal content", "status": "draft"}
    monkeypatch.setattr(li_gen.gen, "get_article", lambda db, aid: article)
    monkeypatch.setattr(li_gen.brands, "get_profile", lambda db, bid: {"name": "Acme"})
    monkeypatch.setattr(li_gen, "ensure_linkedin_agent", AsyncMock(return_value="agent1"))
    client = MagicMock()
    client.run_agent = AsyncMock(return_value={"content": "  A great hook.\n\n#Dev  "})
    out = await li_gen.generate_post(client, MagicMock(), AID, "key_insight")
    assert out == "A great hook.\n\n#Dev"


async def test_generate_post_raises_valueerror_when_no_content(monkeypatch):
    monkeypatch.setattr(
        li_gen.gen, "get_article",
        lambda db, aid: {"id": AID, "business_id": BID, "content_md": "   "},
    )
    with pytest.raises(ValueError):
        await li_gen.generate_post(MagicMock(), MagicMock(), AID, "key_insight")


async def test_generate_post_raises_runtimeerror_on_empty(monkeypatch):
    monkeypatch.setattr(
        li_gen.gen, "get_article",
        lambda db, aid: {"id": AID, "business_id": BID, "content_md": "real", "status": "draft"},
    )
    monkeypatch.setattr(li_gen.brands, "get_profile", lambda db, bid: {})
    monkeypatch.setattr(li_gen, "ensure_linkedin_agent", AsyncMock(return_value="a"))
    client = MagicMock()
    client.run_agent = AsyncMock(return_value={"content": "   "})
    with pytest.raises(RuntimeError):
        await li_gen.generate_post(client, MagicMock(), AID, "key_insight")


async def test_generate_post_caps_body_at_linkedin_limit(monkeypatch):
    monkeypatch.setattr(
        li_gen.gen, "get_article",
        lambda db, aid: {"id": AID, "business_id": BID, "content_md": "real", "status": "draft"},
    )
    monkeypatch.setattr(li_gen.brands, "get_profile", lambda db, bid: {})
    monkeypatch.setattr(li_gen, "ensure_linkedin_agent", AsyncMock(return_value="a"))
    client = MagicMock()
    long_text = "line one\n" + ("x" * 4000)
    client.run_agent = AsyncMock(return_value={"content": long_text})
    out = await li_gen.generate_post(client, MagicMock(), AID, "key_insight")
    assert len(out) <= 3000
    assert out == "line one"  # truncated to the last complete line under the cap


async def test_generate_post_warns_on_token_ceiling(monkeypatch, caplog):
    monkeypatch.setattr(
        li_gen.gen, "get_article",
        lambda db, aid: {"id": AID, "business_id": BID, "content_md": "real", "status": "draft"},
    )
    monkeypatch.setattr(li_gen.brands, "get_profile", lambda db, bid: {})
    monkeypatch.setattr(li_gen, "ensure_linkedin_agent", AsyncMock(return_value="a"))
    client = MagicMock()
    client.run_agent = AsyncMock(
        return_value={"content": "a short post", "stop_reason": "max_tokens"}
    )
    with caplog.at_level("WARNING"):
        await li_gen.generate_post(client, MagicMock(), AID, "key_insight")
    assert any("token ceiling" in r.getMessage() for r in caplog.records)


# --- _resolve_article_url (published-only link rule) ---
def test_resolve_url_none_when_unpublished():
    assert li_gen._resolve_article_url({}, {"id": AID, "status": "draft"}) is None


def test_resolve_url_uses_canonical_when_published(monkeypatch):
    monkeypatch.setattr(li_gen.linking, "canonical_url", lambda b, a: "https://blog.x/p")
    out = li_gen._resolve_article_url({}, {"id": AID, "status": "published"})
    assert out == "https://blog.x/p"


def test_resolve_url_falls_back_to_public_base(monkeypatch):
    monkeypatch.setattr(li_gen.linking, "canonical_url", lambda b, a: None)
    monkeypatch.setattr(
        li_gen, "get_settings",
        lambda: SimpleNamespace(public_base_url="https://app.rf.dev/"),
    )
    out = li_gen._resolve_article_url({}, {"id": AID, "status": "published"})
    assert out == f"https://app.rf.dev/p/{AID}"


def test_resolve_url_none_when_no_canonical_and_no_base(monkeypatch):
    monkeypatch.setattr(li_gen.linking, "canonical_url", lambda b, a: None)
    monkeypatch.setattr(
        li_gen, "get_settings", lambda: SimpleNamespace(public_base_url=None)
    )
    assert li_gen._resolve_article_url({}, {"id": AID, "status": "published"}) is None


def _brand_db():
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": __import__("uuid").UUID(ADMIN_ORG)}
    return db


def _client(db=None, pb=None, user: CurrentUser | None = None):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    app.dependency_overrides[get_powabase] = lambda: pb if pb is not None else MagicMock()
    return TestClient(with_auth(app, user) if user else with_auth(app))


def test_list_linkedin_posts_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "list_posts", lambda db, aid: [
        {"id": PID, "article_id": AID, "angle": "story", "body": "hi",
         "created_by": None, "created_at": "2026-07-16T00:00:00Z",
         "updated_at": "2026-07-16T00:00:00Z"}
    ])
    resp = _client().get(f"/api/articles/{AID}/linkedin-posts")
    assert resp.status_code == 200
    assert resp.json()[0]["angle"] == "story"


def test_generate_linkedin_post_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_gen, "generate_post", AsyncMock(return_value="Hook line.\n\n#Dev"))
    monkeypatch.setattr(li_svc, "create_post", lambda db, **k: {
        "id": PID, "article_id": AID, "angle": k["angle"], "body": k["body"],
        "created_by": None, "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z"})
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 201
    assert resp.json()["body"] == "Hook line.\n\n#Dev"


def test_generate_409_when_article_has_no_content(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    async def boom(*a, **k):
        raise ValueError("article has no content yet")
    monkeypatch.setattr(li_gen, "generate_post", boom)
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 409


def test_generate_502_on_upstream_failure(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    async def boom(*a, **k):
        raise RuntimeError("generation failed")
    monkeypatch.setattr(li_gen, "generate_post", boom)
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 502


def test_generate_propagates_rate_limit_status(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})

    async def boom(*a, **k):
        raise PowabaseError(429, "rate limited")

    monkeypatch.setattr(li_gen, "generate_post", boom)
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 429


def test_generate_502_on_other_powabase_error(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})

    async def boom(*a, **k):
        raise PowabaseError(500, "upstream boom")

    monkeypatch.setattr(li_gen, "generate_post", boom)
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "key_insight"})
    assert resp.status_code == 502


def test_generate_422_on_bad_angle(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    resp = _client().post(f"/api/articles/{AID}/linkedin-posts", json={"angle": "spicy"})
    assert resp.status_code == 422


def test_update_linkedin_post_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "get_post", lambda db, pid: {"id": PID, "article_id": AID})
    monkeypatch.setattr(li_svc, "update_post", lambda db, pid, body: {
        "id": PID, "article_id": AID, "angle": "story", "body": body,
        "created_by": None, "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z"})
    resp = _client().patch(f"/api/articles/{AID}/linkedin-posts/{PID}", json={"body": "edited"})
    assert resp.status_code == 200
    assert resp.json()["body"] == "edited"


def test_update_404_when_post_not_on_article(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "get_post", lambda db, pid: {"id": PID, "article_id": "99999999-9999-9999-9999-999999999999"})
    resp = _client().patch(f"/api/articles/{AID}/linkedin-posts/{PID}", json={"body": "edited"})
    assert resp.status_code == 404


def test_update_404_when_post_deleted_midflight(monkeypatch):
    # get_post passes the guard, but update_post returns None (deleted in between) —
    # must 404, not 500 with a ResponseValidationError.
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "get_post", lambda db, pid: {"id": PID, "article_id": AID})
    monkeypatch.setattr(li_svc, "update_post", lambda db, pid, body: None)
    resp = _client().patch(f"/api/articles/{AID}/linkedin-posts/{PID}", json={"body": "edited"})
    assert resp.status_code == 404


def test_delete_linkedin_post_route(monkeypatch):
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    monkeypatch.setattr(li_svc, "get_post", lambda db, pid: {"id": PID, "article_id": AID})
    monkeypatch.setattr(li_svc, "delete_post", lambda db, pid: True)
    resp = _client().delete(f"/api/articles/{AID}/linkedin-posts/{PID}")
    assert resp.status_code == 204


def test_cross_org_404(monkeypatch):
    # Article's brand is in another org → _guard_article 404s before any work.
    monkeypatch.setattr(gen_svc, "get_article", lambda db, aid: {"id": AID, "business_id": BID})
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": __import__("uuid").UUID("77777777-7777-7777-7777-777777777777")}
    resp = _client(db).get(f"/api/articles/{AID}/linkedin-posts")
    assert resp.status_code == 404


def test_list_brand_posts_service_joins_articles():
    db = MagicMock()
    db.fetch_all.return_value = []
    li_svc.list_posts_for_brand(db, BID)
    sql = db.fetch_all.call_args.args[0].lower()
    assert "join public.articles a on a.id = p.article_id" in sql
    assert "where p.business_id = %s" in sql
    assert "a.title as article_title" in sql
    assert "a.status as article_status" in sql


def test_list_brand_posts_route(monkeypatch):
    monkeypatch.setattr(
        li_svc, "list_posts_for_brand",
        lambda db, bid: [
            {"id": PID, "article_id": AID, "angle": "stat", "body": "hi",
             "created_by": None, "created_at": "2026-07-16T00:00:00Z",
             "updated_at": "2026-07-16T00:00:00Z",
             "article_title": "Governed BaaS", "article_status": "published"}
        ],
    )
    resp = _client().get(f"/api/business-profiles/{BID}/linkedin-posts")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["article_title"] == "Governed BaaS"
    assert body[0]["article_status"] == "published"


def test_list_brand_posts_cross_org_404(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(li_svc, "list_posts_for_brand", called)
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": __import__("uuid").UUID("77777777-7777-7777-7777-777777777777")}
    resp = _client(db).get(f"/api/business-profiles/{BID}/linkedin-posts")
    assert resp.status_code == 404
    called.assert_not_called()
