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
CID = "88888888-8888-8888-8888-888888888888"
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
    # a token-less pattern can't make a per-article URL → None (else every article
    # would resolve to the SAME url and all internal links would collide)
    assert linking.canonical_url(
        {"url_pattern": "https://blog.example.com/"}, {"id": TID, "slug": "guide"}
    ) is None


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


def test_apply_inserts_link_rescores_and_accepts(monkeypatch):
    db = MagicMock()
    # 1) fetch the suggestion, 2) _set_status ... returning (no brief_id → no brief fetch)
    db.fetch_one.side_effect = [
        {"id": SID, "article_id": AID, "target_article_id": TID,
         "anchor_text": "headless cms",
         "target_url": "https://blog.x.com/guide", "status": "pending"},
        {"id": SID, "status": "accepted"},
    ]
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "We weigh headless cms options.", "title": "T"},
    )
    monkeypatch.setattr(linking, "resolve_links", lambda d, b, md, **k: md)
    updates: dict = {}
    monkeypatch.setattr(
        linking.gen_svc, "_update", lambda d, aid, **f: updates.update(f)
    )
    out = linking.apply_suggestion(db, BID, SID)
    # The BODY stores a stable ref, not the URL — so the link follows the target's slug.
    assert f"[headless cms](rf:article/{TID})" in updates["content_md"]
    assert "seo_score" in updates  # re-scored deterministically (no LLM call)
    assert out["status"] == "accepted"


def test_apply_dismisses_a_stale_anchor(monkeypatch):
    db = MagicMock()
    db.fetch_one.side_effect = [
        {"id": SID, "article_id": AID, "anchor_text": "headless cms",
         "target_url": "https://blog.x.com/guide", "status": "pending"},
        {"id": SID, "status": "dismissed"},
    ]
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "the phrase is gone now"},
    )
    out = linking.apply_suggestion(db, BID, SID)
    assert out["status"] == "dismissed"


def test_apply_noop_when_not_pending():
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": SID, "article_id": AID, "anchor_text": "x",
        "target_url": "https://blog.x.com/guide", "status": "accepted",
    }
    assert linking.apply_suggestion(db, BID, SID) is None


# --- stable internal-link refs → live URLs at render time ---
def test_resolve_links_replaces_refs_with_canonical(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.brands, "get_profile",
        lambda d, b: {"url_pattern": "https://blog.x.com/{slug}"},
    )
    db.fetch_all.return_value = [
        {"id": TID, "title": "T", "slug": "headless-guide", "keywords": [],
         "canonical_url": None},
    ]
    out = linking.resolve_links(db, BID, f"See [the guide](rf:article/{TID}) now.")
    assert out == "See [the guide](https://blog.x.com/headless-guide) now."


def test_resolve_links_noop_without_refs():
    db = MagicMock()
    assert linking.resolve_links(db, BID, "no internal links here") == (
        "no internal links here"
    )
    db.fetch_all.assert_not_called()


def test_resolve_links_falls_back_when_target_missing(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.brands, "get_profile", lambda d, b: {"url_pattern": "https://x/{slug}"}
    )
    db.fetch_all.return_value = []  # target deleted
    out = linking.resolve_links(db, BID, f"x [a](rf:article/{TID}) y")
    assert f"/p/{TID}" in out


def test_mask_restore_round_trips_refs():
    md = f"intro [a](rf:article/{TID}) and [b](rf:article/{AID}) end"
    masked, mapping = linking.mask_refs(md)
    assert "rf:article/" not in masked  # refs hidden from the LLM
    assert linking.restore_refs(masked, mapping) == md


def test_restore_refs_survives_anchor_reword():
    """The reviser may reword the anchor/prose but keeps the masked token verbatim."""
    md = f"[x](rf:article/{TID})"
    masked, mapping = linking.mask_refs(md)
    rewritten = masked.replace("[x]", "[the related guide]")
    assert linking.restore_refs(rewritten, mapping) == (
        f"[the related guide](rf:article/{TID})"
    )


def test_mask_restore_round_trips_with_many_refs():
    """≥11 refs: sentinel `rfref:1` is a prefix of `rfref:10`/`rfref:11`, so a naive
    insertion-order restore corrupts the longer tokens. Round-trip must survive."""
    md = " ".join(
        f"[link{i}](rf:article/{i:08d}-0000-0000-0000-000000000000)"
        for i in range(12)
    )
    masked, mapping = linking.mask_refs(md)
    assert len(mapping) == 12
    assert "rf:article/" not in masked
    assert linking.restore_refs(masked, mapping) == md


# --- cluster-aware linking (structural + gaps) ---
def test_structural_targets_member_returns_its_pillar():
    db = MagicMock()
    pillar = {"id": TID, "title": "Pillar", "slug": "p", "keywords": [],
              "canonical_url": None}
    db.fetch_one.side_effect = [{"pillar_article_id": TID}, pillar]
    out = linking._structural_targets(
        db, {"id": AID, "cluster_id": CID, "cluster_role": "member"}
    )
    assert out == [(pillar, "pillar")]


def test_structural_targets_pillar_returns_members():
    db = MagicMock()
    m = {"id": "m1", "title": "M1", "slug": "m1", "keywords": [], "canonical_url": None}
    db.fetch_all.return_value = [m]
    out = linking._structural_targets(
        db, {"id": AID, "cluster_id": CID, "cluster_role": "pillar"}
    )
    assert out == [(m, "member")]


def test_structural_targets_empty_without_cluster():
    assert linking._structural_targets(MagicMock(), {"id": AID}) == []


def test_suggest_stages_a_gap_for_an_unmentioned_pillar(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "nothing relevant here at all",
                        "cluster_id": CID, "cluster_role": "member"},
    )
    monkeypatch.setattr(linking.brands, "get_profile", lambda d, bid: _PATTERN_BRAND)
    monkeypatch.setattr(
        linking, "_structural_targets",
        lambda d, a: [({"id": TID, "title": "Pillar Guide", "slug": "guide",
                        "keywords": ["unmatched phrase"], "canonical_url": None},
                       "pillar")],
    )
    monkeypatch.setattr(linking, "_link_targets", lambda d, bid, aid: [])
    db.fetch_one.return_value = {**SUGGESTION, "anchor_text": None, "kind": "pillar"}
    out = linking.suggest_links(db, BID, AID)
    assert len(out) == 1
    _, p = db.fetch_one.call_args[0]
    assert p[3] is None  # anchor null → a gap
    assert p[7] == "pillar"  # structural up-link


def test_suggest_stages_structural_link_when_pillar_is_mentioned(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "We cover headless cms in depth.",
                        "cluster_id": CID, "cluster_role": "member"},
    )
    monkeypatch.setattr(linking.brands, "get_profile", lambda d, bid: _PATTERN_BRAND)
    monkeypatch.setattr(
        linking, "_structural_targets",
        lambda d, a: [({"id": TID, "title": "Headless CMS Guide", "slug": "guide",
                        "keywords": ["headless cms"], "canonical_url": None},
                       "pillar")],
    )
    monkeypatch.setattr(linking, "_link_targets", lambda d, bid, aid: [])
    db.fetch_one.return_value = {**SUGGESTION}
    linking.suggest_links(db, BID, AID)
    _, p = db.fetch_one.call_args[0]
    assert p[3] == "headless cms"  # natural anchor found
    assert p[7] == "pillar"  # tagged as the structural up-link


def test_anchored_suggestion_supersedes_a_pending_gap(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "We cover headless cms here.",
                        "cluster_id": CID, "cluster_role": "member"},
    )
    monkeypatch.setattr(linking.brands, "get_profile", lambda d, bid: _PATTERN_BRAND)
    monkeypatch.setattr(
        linking, "_structural_targets",
        lambda d, a: [({"id": TID, "title": "Guide", "slug": "g",
                        "keywords": ["headless cms"], "canonical_url": None}, "pillar")],
    )
    monkeypatch.setattr(linking, "_link_targets", lambda d, bid, aid: [])
    db.fetch_one.return_value = {**SUGGESTION}
    linking.suggest_links(db, BID, AID)
    deletes = [c.args[0] for c in db.execute.call_args_list]
    assert any(
        "delete from public.link_suggestions" in q and "anchor_text is null" in q
        for q in deletes
    )


def test_apply_rejects_a_gap():
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": SID, "article_id": AID, "anchor_text": None,
        "target_url": "https://b.com/g", "status": "pending",
    }
    assert linking.apply_suggestion(db, BID, SID) is None


async def test_generate_gap_link_inserts_and_accepts(monkeypatch):
    db = MagicMock()
    db.fetch_one.side_effect = [
        {"id": SID, "article_id": AID, "target_article_id": TID, "anchor_text": None,
         "target_url": "https://b.com/guide", "target_title": "Guide",
         "status": "pending"},
        {"id": SID, "status": "accepted"},
    ]
    monkeypatch.setattr(
        linking.gen_svc, "get_article",
        lambda d, aid: {"content_md": "# Title\n\nIntro para.\n\nMore body.",
                        "title": "T"},
    )
    monkeypatch.setattr(linking, "_ensure_linker", AsyncMock(return_value="lk"))
    monkeypatch.setattr(linking, "resolve_links", lambda d, b, md, **k: md)
    client = MagicMock()
    client.run_agent = AsyncMock(
        return_value={"content": "For the basics, see [the guide](https://b.com/guide)."}
    )
    updates: dict = {}
    monkeypatch.setattr(linking.gen_svc, "_update", lambda d, aid, **f: updates.update(f))
    out = await linking.generate_gap_link(client, db, BID, SID)
    # The model's URL is swapped for a stable ref before storing.
    assert f"[the guide](rf:article/{TID})" in updates["content_md"]
    assert "seo_score" in updates  # re-scored deterministically
    assert out["status"] == "accepted"


async def test_generate_gap_link_rejects_non_gap():
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": SID, "article_id": AID, "anchor_text": "x",
        "target_url": "u", "target_title": "t", "status": "pending",
    }
    assert await linking.generate_gap_link(MagicMock(), db, BID, SID) is None


async def test_generate_gap_link_rejects_output_without_the_link(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": SID, "article_id": AID, "anchor_text": None,
        "target_url": "https://b.com/guide", "target_title": "Guide",
        "status": "pending",
    }
    monkeypatch.setattr(linking.gen_svc, "get_article", lambda d, aid: {"content_md": "x"})
    monkeypatch.setattr(linking, "_ensure_linker", AsyncMock(return_value="lk"))
    client = MagicMock()
    client.run_agent = AsyncMock(return_value={"content": "a sentence with no link"})
    assert await linking.generate_gap_link(client, db, BID, SID) is None


def test_generate_link_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)

    async def fake_gen(c, d, bid, sid):
        return {**SUGGESTION, "anchor_text": None, "kind": "pillar",
                "status": "accepted"}

    monkeypatch.setattr(linking, "generate_gap_link", fake_gen)
    resp = _client().post(f"/api/articles/{AID}/links/{SID}/generate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


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
    monkeypatch.setattr(
        linking, "apply_suggestion",
        lambda d, bid, sid: {**SUGGESTION, "status": "accepted"},
    )
    resp = _client().post(f"/api/articles/{AID}/links/{SID}/apply")
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_apply_link_404_when_missing(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(linking, "apply_suggestion", lambda d, bid, sid: None)
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
