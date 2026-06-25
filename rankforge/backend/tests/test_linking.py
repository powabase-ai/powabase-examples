"""Internal linking (M6 / Phase 12.1) — deterministic anchor logic + route wiring."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import generation as gsvc
from rankforge_backend.services import linking

BID = "11111111-1111-1111-1111-111111111111"
AID = "55555555-5555-5555-5555-555555555555"
TID = "66666666-6666-6666-6666-666666666666"
SID = "77777777-7777-7777-7777-777777777777"
ARTICLE = {"id": AID, "business_id": BID, "status": "draft"}
SUGGESTION = {
    "id": SID, "business_id": BID, "article_id": AID, "target_article_id": TID,
    "anchor_text": "headless cms", "target_url": f"/p/{TID}",
    "target_title": "Guide", "reason": "links to your guide", "status": "pending",
    "created_at": "2026-06-20T00:00:00Z",
}


# --- deterministic anchor logic (unit) ---
def test_linkable_mask_blocks_links_code_and_headings():
    md = "# Heading here\n\nplain text and `code` then [anchor](/p/x) end."
    mask = linking._linkable_mask(md)
    i = md.index("plain")
    assert all(mask[i + j] for j in range(len("plain")))  # body text is linkable
    j = md.index("anchor")
    assert not any(mask[j + k] for k in range(len("anchor")))  # inside a link: no
    assert not mask[md.index("Heading")]  # heading line: no
    assert not mask[md.index("code")]  # inline code: no


def test_find_anchor_is_whole_word_and_safe():
    md = "covers microservices and micro patterns"
    mask = linking._linkable_mask(md)
    assert linking._find_anchor(md, mask, "micro")  # the standalone word
    # 'service' only appears inside 'microservices' → not a whole-word match
    assert linking._find_anchor(md, mask, "service") is None


def test_anchor_candidates_prefers_specific_and_filters_short():
    cands = linking._anchor_candidates(
        {"keywords": ["seo", "headless cms"], "title": "Headless CMS Guide"}
    )
    assert "seo" not in cands  # too short (< 4 chars)
    assert cands[0] == "Headless CMS Guide"  # longest/most specific first
    assert "headless cms" in cands


_PATTERN_BRAND = {"url_pattern": "https://blog.example.com/{slug}"}


def test_canonical_url_resolution():
    art = {"id": TID, "slug": "guide"}
    # override wins
    assert linking.canonical_url(
        _PATTERN_BRAND, {**art, "canonical_url": "https://x.com/custom"}
    ) == "https://x.com/custom"
    # else the brand pattern renders
    assert linking.canonical_url(_PATTERN_BRAND, art) == "https://blog.example.com/guide"
    # no pattern + no override → undeterminable
    assert linking.canonical_url({}, art) is None
    # {slug} pattern but the article has no slug → None (no empty path segment)
    assert linking.canonical_url(_PATTERN_BRAND, {"id": TID, "slug": ""}) is None


def test_suggest_links_requires_a_brand_pattern(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "We weigh headless cms options."},
    )
    monkeypatch.setattr(linking.brands, "get_profile", lambda d, bid: {})  # no pattern
    assert linking.suggest_links(db, BID, AID) == []
    db.fetch_all.assert_not_called()  # bailed before scanning targets


def test_suggest_links_stages_a_verbatim_match(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "We weigh headless cms options."},
    )
    monkeypatch.setattr(linking.brands, "get_profile", lambda d, bid: _PATTERN_BRAND)
    db.fetch_all.return_value = [
        {"id": TID, "title": "Headless CMS Guide", "slug": "guide",
         "keywords": ["headless cms"], "canonical_url": None}
    ]
    db.fetch_one.return_value = {**SUGGESTION}
    out = linking.suggest_links(db, BID, AID)
    assert len(out) == 1
    q, p = db.fetch_one.call_args[0]
    assert "insert into public.link_suggestions" in q
    assert p[3] == "headless cms"  # the verbatim anchor span found in the body
    assert p[4] == "https://blog.example.com/guide"  # resolved from the brand pattern


def test_suggest_links_skips_text_already_linked(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "See [headless cms](/p/z) already."},
    )
    monkeypatch.setattr(linking.brands, "get_profile", lambda d, bid: _PATTERN_BRAND)
    db.fetch_all.return_value = [
        {"id": TID, "title": "X", "slug": "guide", "keywords": ["headless cms"],
         "canonical_url": None}
    ]
    assert linking.suggest_links(db, BID, AID) == []
    db.fetch_one.assert_not_called()  # nothing to stage


async def test_apply_inserts_link_rescores_and_accepts(monkeypatch):
    db = MagicMock()
    db.fetch_one.side_effect = [
        {"id": SID, "article_id": AID, "anchor_text": "headless cms",
         "target_url": f"/p/{TID}", "status": "pending"},
        {"id": SID, "status": "accepted"},
    ]
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "We weigh headless cms options."},
    )
    updates: dict = {}
    monkeypatch.setattr(
        linking.gen_svc, "_update", lambda d, aid, **f: updates.update(f)
    )
    score = AsyncMock()
    monkeypatch.setattr("rankforge_backend.services.scoring.score_and_store", score)
    out = await linking.apply_suggestion(MagicMock(), db, BID, SID)
    assert f"[headless cms](/p/{TID})" in updates["content_md"]  # link inserted
    score.assert_awaited_once()  # re-scored
    assert out["status"] == "accepted"


async def test_apply_dismisses_a_stale_anchor(monkeypatch):
    db = MagicMock()
    db.fetch_one.side_effect = [
        {"id": SID, "article_id": AID, "anchor_text": "headless cms",
         "target_url": "/p/x", "status": "pending"},
        {"id": SID, "status": "dismissed"},
    ]
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "the phrase is gone now"},
    )
    out = await linking.apply_suggestion(MagicMock(), db, BID, SID)
    assert out["status"] == "dismissed"


async def test_apply_noop_when_not_pending():
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": SID, "article_id": AID, "anchor_text": "x",
        "target_url": "/p/x", "status": "accepted",
    }
    assert await linking.apply_suggestion(MagicMock(), db, BID, SID) is None


# --- routes (hermetic) ---
def _brand_db() -> MagicMock:
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db=None, user: CurrentUser | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app, user) if user else with_auth(app))


def test_list_links_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(linking, "list_suggestions", lambda d, aid: [SUGGESTION])
    resp = _client().get(f"/api/articles/{AID}/links")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == SID


def test_suggest_links_route_runs(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(linking, "suggest_links", lambda d, bid, aid: None)
    monkeypatch.setattr(linking, "list_suggestions", lambda d, aid: [SUGGESTION])
    resp = _client().post(f"/api/articles/{AID}/links/suggest")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_suggest_links_requires_editor(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(f"/api/articles/{AID}/links/suggest")
    assert resp.status_code == 403


def test_apply_link_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)

    async def fake_apply(c, d, bid, sid):
        return {**SUGGESTION, "status": "accepted"}

    monkeypatch.setattr(linking, "apply_suggestion", fake_apply)
    resp = _client().post(f"/api/articles/{AID}/links/{SID}/apply")
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_apply_link_404_when_missing(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)

    async def fake_apply(c, d, bid, sid):
        return None

    monkeypatch.setattr(linking, "apply_suggestion", fake_apply)
    resp = _client().post(f"/api/articles/{AID}/links/{SID}/apply")
    assert resp.status_code == 404


def test_dismiss_link_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(
        linking, "dismiss_suggestion",
        lambda d, bid, sid: {**SUGGESTION, "status": "dismissed"},
    )
    resp = _client().post(f"/api/articles/{AID}/links/{SID}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"
