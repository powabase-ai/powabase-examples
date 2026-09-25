"""business_profiles routes — hermetic, Database mocked at the boundary."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, ADMIN_USER, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes import business_profiles as bp_route
from rankforge_backend.routes.business_profiles import get_db, get_powabase

ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Acme",
    "domain": "acme.com",
    "description": None,
    "niche": "SaaS",
    "audience": None,
    "seed_topics": ["seo"],
    "target_keywords": [],
    "competitors": [{"name": "Rival", "domain": "rival.com"}],
    "brand_kb_id": None,
    "sitemap_url": None,
    "created_by": None,
    "created_at": "2026-06-18T00:00:00Z",
    "updated_at": "2026-06-18T00:00:00Z",
}


def make_client(db: MagicMock, pb: MagicMock | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: pb or MagicMock()
    return TestClient(with_auth(app))


LOGO_ROUTE = f"/api/business-profiles/{ROW['id']}/logo"


def test_upload_logo_rejects_non_image():
    resp = make_client(MagicMock()).post(
        LOGO_ROUTE, files={"file": ("notes.txt", b"hi", "text/plain")}
    )
    assert resp.status_code == 400


def test_upload_logo_uploads_to_storage_and_stores_url(monkeypatch):
    monkeypatch.setattr(
        bp_route.svc, "get_profile",
        lambda d, pid: {**ROW, "org_id": UUID(ADMIN_ORG)},
    )
    monkeypatch.setattr(
        bp_route.svc, "update_profile",
        lambda d, pid, payload, org: {**ROW, "logo_url": payload.logo_url},
    )
    pb = MagicMock()
    pub = f"https://proj/storage/v1/object/public/brand-logos/{ROW['id']}.png"
    pb.upload_public_object = AsyncMock(return_value=pub)

    resp = make_client(MagicMock(), pb).post(
        LOGO_ROUTE, files={"file": ("logo.png", b"\x89PNG\r\n", "image/png")}
    )
    assert resp.status_code == 200
    pb.upload_public_object.assert_awaited_once()
    assert resp.json()["logo_url"].startswith(pub)  # stored URL (+ cache-buster)
    # The object key is namespaced by org so it isn't a bare, guessable brand id in the
    # shared public bucket (no cross-org overwrite).
    bucket, path = pb.upload_public_object.await_args.args[:2]
    assert bucket == "brand-logos"
    assert path == f"{ADMIN_ORG}/{ROW['id']}.png"


def test_upload_logo_404_when_brand_in_another_org(monkeypatch):
    # Brand exists but in a different org → 404, and nothing is uploaded.
    monkeypatch.setattr(
        bp_route.svc, "get_profile",
        lambda d, pid: {**ROW, "org_id": UUID("00000000-0000-0000-0000-0000000000ff")},
    )
    pb = MagicMock()
    pb.upload_public_object = AsyncMock()
    resp = make_client(MagicMock(), pb).post(
        LOGO_ROUTE, files={"file": ("logo.png", b"\x89PNG", "image/png")}
    )
    assert resp.status_code == 404
    pb.upload_public_object.assert_not_awaited()


def test_create_inserts_and_returns_201():
    db = MagicMock()
    # first fetch_one = name_exists (None → free), second = insert RETURNING
    db.fetch_one.side_effect = [None, ROW]
    client = make_client(db)

    resp = client.post(
        "/api/business-profiles",
        json={
            "name": "Acme",
            "domain": "acme.com",
            "niche": "SaaS",
            "seed_topics": ["seo"],
            "competitors": [{"name": "Rival", "domain": "rival.com"}],
            "url_pattern": "https://blog.acme.com/{slug}",
        },
    )

    assert resp.status_code == 201
    assert resp.json()["name"] == "Acme"
    assert resp.json()["competitors"][0]["domain"] == "rival.com"
    sql = db.fetch_one.call_args.args[0].lower()
    assert "insert into public.business_profiles" in sql
    # url_pattern must be persisted at create time, not silently dropped
    assert "url_pattern" in sql
    assert "https://blog.acme.com/{slug}" in db.fetch_one.call_args.args[1]


def test_create_duplicate_name_returns_409():
    db = MagicMock()
    db.fetch_one.return_value = {"exists": 1}  # name_exists → truthy
    client = make_client(db)
    resp = client.post("/api/business-profiles", json={"name": "Acme"})
    assert resp.status_code == 409


def test_list_returns_profiles():
    db = MagicMock()
    db.fetch_all.return_value = [ROW]
    client = make_client(db)

    resp = client.get("/api/business-profiles")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == ROW["id"]


def test_get_missing_returns_404():
    db = MagicMock()
    db.fetch_one.return_value = None
    client = make_client(db)

    resp = client.get(
        "/api/business-profiles/22222222-2222-2222-2222-222222222222"
    )
    assert resp.status_code == 404


def test_update_sends_only_changed_fields():
    db = MagicMock()
    db.fetch_one.return_value = {**ROW, "niche": "Fintech"}
    client = make_client(db)

    resp = client.patch(
        "/api/business-profiles/11111111-1111-1111-1111-111111111111",
        json={"niche": "Fintech"},
    )
    assert resp.status_code == 200
    assert resp.json()["niche"] == "Fintech"
    sql = db.fetch_one.call_args.args[0].lower()
    assert "niche = %s" in sql
    assert "name = %s" not in sql  # unchanged field not in the UPDATE


def test_db_unconfigured_returns_503():
    # No dependency override → get_db sees app.state.db is None (hermetic lifespan).
    with TestClient(create_app()) as client:
        resp = client.get("/api/business-profiles")
    assert resp.status_code == 503


def test_delete_cleans_kbs_and_unshared_sources(monkeypatch):
    """Deleting a workspace also deletes its KBs and exactly the Source ids that
    brand_exclusive_source_ids returns — the route passes those through to Powabase
    (the exclusivity logic itself lives in source_refs and is tested there)."""
    db = MagicMock()
    db.fetch_one.return_value = {
        **ROW,
        "org_id": ADMIN_USER.org_id,
        "brand_kb_id": "kb_b",
        "materials_kb_id": "kb_m",
        "cluster_kb_id": "kb_c",
    }
    monkeypatch.setattr(
        bp_route.source_refs,
        "brand_exclusive_source_ids",
        lambda d, bid: ["s1", "s2"],
    )
    monkeypatch.setattr(bp_route.svc, "delete_profile", lambda d, pid, oid: None)
    pb = MagicMock()
    pb.delete_kb = AsyncMock()
    pb.delete_source = AsyncMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.state.powabase = pb
    resp = TestClient(with_auth(app)).delete(f"/api/business-profiles/{ROW['id']}")

    assert resp.status_code == 204
    assert pb.delete_kb.await_count == 3  # grounding + materials + cluster KBs
    # Exactly the ids brand_exclusive_source_ids returned are deleted — nothing more.
    deleted = {c.args[0] for c in pb.delete_source.await_args_list}
    assert deleted == {"s1", "s2"}


def test_delete_without_powabase_still_returns_204(monkeypatch):
    """Powabase cleanup is best-effort: when app.state.powabase is falsy, the delete
    still drops the tracking rows and returns 204 (the project Sources are left, but
    the workspace is gone)."""
    db = MagicMock()
    db.fetch_one.return_value = {**ROW, "org_id": ADMIN_USER.org_id}
    deleted = {}
    monkeypatch.setattr(
        bp_route.svc, "delete_profile",
        lambda d, pid, oid: deleted.update(pid=pid),
    )

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.state.powabase = None
    resp = TestClient(with_auth(app)).delete(f"/api/business-profiles/{ROW['id']}")

    assert resp.status_code == 204
    assert deleted.get("pid") is not None  # the row delete still ran


def test_list_tolerates_an_invalid_stored_blog_profile():
    """One brand with a corrupt stored blog_profile must not 500 the whole list; the
    raw value is returned so the settings page can show and fix it."""
    db = MagicMock()
    bad = {"categories": "nope"}
    db.fetch_all.return_value = [{**ROW, "blog_profile": bad}]
    resp = make_client(db).get("/api/business-profiles")
    assert resp.status_code == 200
    assert resp.json()[0]["blog_profile"] == bad


def test_update_still_validates_the_blog_profile_strictly():
    db = MagicMock()
    resp = make_client(db).patch(
        f"/api/business-profiles/{ROW['id']}",
        json={"blog_profile": {"categories": [{"key": "rag", "label": "R"}],
                               "link": {"min": 1}}},
    )
    assert resp.status_code == 422
    db.fetch_one.assert_not_called()


# --- url_pattern is validated when saved (PR #26 review round 2) ---
_BAD_PATTERNS = {
    "blog.acme.com/{slug}": "http(s)",  # no scheme and not a site path
    "//acme.com/{slug}": "http(s)",  # protocol-relative
    "ftp://acme.com/{slug}": "http(s)",
    "https:///{slug}": "http(s)",  # no host
    "https://acme.com/blog/": "{slug}",  # no token
    "https://acme.com/blog/{slug}#top": "#",
    "https://acme.com/blog/ {slug}": "whitespace",
    # Browsers read '\\' as '/': these render as off-site links (round 3, F2).
    "/\\evil.com/{slug}": "'\\'",
    "https://acme.com/\\evil/{slug}": "'\\'",
}


def test_create_rejects_a_bad_url_pattern_with_a_clear_422():
    for pattern, hint in _BAD_PATTERNS.items():
        db = MagicMock()
        resp = make_client(db).post(
            "/api/business-profiles", json={"name": "Acme", "url_pattern": pattern}
        )
        assert resp.status_code == 422, pattern
        msg = " ".join(e["msg"] for e in resp.json()["detail"])
        assert "url_pattern" in msg and hint in msg, (pattern, msg)
        db.fetch_one.assert_not_called()


def _patch_db(stored: str | None, org: str = ADMIN_ORG) -> MagicMock:
    """A db whose brand read returns a row with `stored` as its url_pattern; the
    update echoes the row back."""
    db = MagicMock()
    row = {**ROW, "url_pattern": stored, "org_id": UUID(org)}
    db.fetch_one.side_effect = lambda q, p=None: row
    return db


def _updates(db: MagicMock) -> list:
    return [c for c in db.fetch_one.call_args_list
            if "update public.business_profiles" in c.args[0]]


def test_update_rejects_a_bad_url_pattern():
    for pattern, hint in _BAD_PATTERNS.items():
        db = _patch_db("https://acme.com/blog/{slug}")
        resp = make_client(db).patch(
            f"/api/business-profiles/{ROW['id']}", json={"url_pattern": pattern},
        )
        assert resp.status_code == 422, pattern
        err = resp.json()["detail"][0]
        assert err["loc"] == ["body", "url_pattern"]
        assert "url_pattern" in err["msg"] and hint in err["msg"], (pattern, err)
        assert _updates(db) == []


# --- review r3 minor: a legacy pattern, sent back unchanged, doesn't block the
# rest of the settings form (create stays strict) ---
def test_update_accepts_the_unchanged_legacy_url_pattern():
    legacy = "blog/{slug}#x"
    db = _patch_db(legacy)
    resp = make_client(db).patch(
        f"/api/business-profiles/{ROW['id']}",
        json={"name": "Acme 2", "url_pattern": f"  {legacy} "},
    )
    assert resp.status_code == 200
    assert len(_updates(db)) == 1
    # A legacy value stored with stray spaces compares by its stripped form.
    db = _patch_db(f" {legacy}\n")
    resp = make_client(db).patch(
        f"/api/business-profiles/{ROW['id']}", json={"url_pattern": legacy},
    )
    assert resp.status_code == 200


def test_update_rejects_a_changed_bad_url_pattern_even_with_a_legacy_one():
    db = _patch_db("blog/{slug}#x")
    resp = make_client(db).patch(
        f"/api/business-profiles/{ROW['id']}", json={"url_pattern": "blog/{slug}#y"},
    )
    assert resp.status_code == 422 and _updates(db) == []


def test_update_never_compares_against_another_orgs_pattern():
    legacy = "blog/{slug}#x"
    db = _patch_db(legacy, org="99999999-9999-9999-9999-999999999999")
    resp = make_client(db).patch(
        f"/api/business-profiles/{ROW['id']}", json={"url_pattern": legacy},
    )
    assert resp.status_code == 422 and _updates(db) == []


def test_update_without_url_pattern_skips_the_check():
    db = _patch_db("blog/{slug}#x")
    resp = make_client(db).patch(
        f"/api/business-profiles/{ROW['id']}", json={"name": "Acme 2"},
    )
    assert resp.status_code == 200 and len(_updates(db)) == 1


def test_create_rejects_a_legacy_style_pattern():
    db = MagicMock()
    resp = make_client(db).post(
        "/api/business-profiles", json={"name": "Acme", "url_pattern": "blog/{slug}#x"}
    )
    assert resp.status_code == 422
    db.fetch_one.assert_not_called()


def test_good_url_patterns_are_accepted():
    from rankforge_backend.models.business import BusinessProfileCreate as C
    from rankforge_backend.models.business import BusinessProfileUpdate as U

    for pattern in ["https://blog.acme.com/{slug}", "http://acme.com/p/{id}/",
                    "/blog/{slug}/", "https://acme.com/blog/{slug}?ref=rf"]:
        assert C(name="A", url_pattern=pattern).url_pattern == pattern
        db = _patch_db(None)
        resp = make_client(db).patch(
            f"/api/business-profiles/{ROW['id']}", json={"url_pattern": pattern},
        )
        assert resp.status_code == 200, pattern
    assert U(url_pattern="  /blog/{slug}  ").url_pattern == "/blog/{slug}"
    assert U(url_pattern=None).url_pattern is None
    assert U(url_pattern="  ").url_pattern is None  # blank clears it


def test_list_still_reads_a_legacy_url_pattern():
    db = MagicMock()
    db.fetch_all.return_value = [{**ROW, "url_pattern": "blog/{slug}#x"}]
    resp = make_client(db).get("/api/business-profiles")
    assert resp.status_code == 200
    assert resp.json()[0]["url_pattern"] == "blog/{slug}#x"
