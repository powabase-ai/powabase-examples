"""Publishing/export — rendering (pure), service, and route wiring (hermetic)."""

from unittest.mock import MagicMock
from uuid import UUID

import pytest
from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.routes.business_profiles import get_db
from rankforge_backend.routes.research import get_powabase
from rankforge_backend.services import publishing as svc

AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"
ARTICLE = {
    "id": AID,
    "title": "Title",
    "slug": "title",
    "meta_title": "Title",
    "meta_description": "A description.",
    "content_md": "# Heading\n\nBody text.\n\n<script>alert(1)</script>",
    "json_ld": {"@type": "BlogPosting"},
    "keywords": ["kw"],
}


# --- rendering (pure) ---
def test_render_body_strips_scripts():
    h = svc.render_body_html("# A\n\n<script>alert(1)</script>\n\nhi")
    assert "<script" not in h
    assert "<h1>A</h1>" in h


def test_render_standalone_escapes_jsonld():
    doc = svc.render_standalone_html({**ARTICLE, "json_ld": {"x": "</script>"}})
    assert "<!doctype html>" in doc
    assert "\\u003c/script>" in doc  # closing-tag escaped


def test_render_markdown_frontmatter_shape():
    out = svc.render_markdown(ARTICLE)
    assert out.startswith("---\n")
    assert 'title: "Title"' in out
    assert 'description: "A description."' in out
    assert "tags:\n  - " in out and '"kw"' in out
    assert "draft: true" in out  # ARTICLE has no status → not publishable
    # The body's leading '# Heading' H1 is stripped (the blog renders `title` as the
    # page h1) — a body H1 would duplicate it. The body starts after a blank line.
    assert "---\n\nBody text." in out
    assert "# Heading" not in out


def test_render_markdown_strips_only_the_leading_h1():
    # A '## ' subheading is NOT an H1 → kept; only the single leading '# ' is dropped.
    out = svc.render_markdown({**ARTICLE, "content_md": "# Title\n\n## Section\n\nx"})
    assert "## Section" in out
    assert out.count("# ") == 1  # just the surviving '## '


def test_render_markdown_published_is_dated_and_not_draft():
    out = svc.render_markdown(
        {**ARTICLE, "status": "published", "updated_at": "2026-06-25T12:00:00Z"}
    )
    assert "publishedDate: 2026-06-25" in out
    assert "draft: false" in out


def test_render_markdown_approved_is_not_draft():
    # Exporting an APPROVED (editorially signed-off) article should seed the live blog.
    out = svc.render_markdown({**ARTICLE, "status": "approved"})
    assert "draft: false" in out


def test_render_markdown_in_review_stays_draft():
    out = svc.render_markdown({**ARTICLE, "status": "in_review"})
    assert "draft: true" in out


def test_render_standalone_has_single_h1():
    # The document emits <h1>{title}</h1>; the body's own leading H1 is stripped so the
    # page isn't double-titled.
    doc = svc.render_standalone_html({**ARTICLE, "content_md": "# Title\n\nBody."})
    assert doc.count("<h1>") == 1


def test_export_markdown_fills_author_and_tags_and_export_date(monkeypatch):
    from datetime import UTC, datetime

    from rankforge_backend.services import business_profiles as brands

    db = MagicMock()
    db.fetch_one.return_value = {"keywords": ["auth", "sso"]}  # the keywords select
    monkeypatch.setattr(
        svc.gen_svc, "get_article",
        lambda d, aid: {**ARTICLE, "business_id": BID, "status": "published",
                        "author": None, "updated_at": "2026-06-25T00:00:00Z"},
    )
    monkeypatch.setattr(brands, "get_profile", lambda d, bid: {"name": "Acme"})
    monkeypatch.setattr(svc.linking, "resolve_links", lambda d, bid, md: md)

    content, media = svc.export(db, AID, "markdown")
    assert media == "text/markdown"
    assert 'author: "Acme Team"' in content  # no override / default → "<Brand> Team"
    assert '"auth"' in content and '"sso"' in content  # keywords → tags
    assert "draft: false" in content
    # A published article with NO publication record yet (first export) → today.
    today = datetime.now(UTC).date().isoformat()
    assert f"publishedDate: {today}" in content


def test_export_reuses_first_publication_date_for_published(monkeypatch):
    # Re-exporting a live post must NOT churn its date — it uses the first successful
    # publication's date, stable across re-exports.
    from rankforge_backend.services import business_profiles as brands

    db = MagicMock()
    # 1st fetch_one = keywords select; 2nd = min(published_at) for the publish date.
    db.fetch_one.side_effect = [
        {"keywords": []},
        {"first": "2026-06-01T09:00:00Z"},
    ]
    monkeypatch.setattr(svc.linking, "resolve_links", lambda d, bid, md: md)
    monkeypatch.setattr(brands, "get_profile", lambda d, bid: {"name": "Acme"})
    monkeypatch.setattr(
        svc.gen_svc, "get_article",
        lambda d, aid: {**ARTICLE, "business_id": BID, "status": "published",
                        "author": "A"},
    )
    content, _ = svc.export(db, AID, "markdown")
    assert "publishedDate: 2026-06-01" in content  # stable, not today


def test_export_author_override_beats_brand_default(monkeypatch):
    from rankforge_backend.services import business_profiles as brands

    db = MagicMock()
    db.fetch_one.return_value = {"keywords": []}
    monkeypatch.setattr(svc.linking, "resolve_links", lambda d, bid, md: md)
    monkeypatch.setattr(
        brands, "get_profile",
        lambda d, bid: {"name": "Acme", "default_author": "Acme Editorial"},
    )
    # No per-article override → brand default_author.
    monkeypatch.setattr(
        svc.gen_svc, "get_article",
        lambda d, aid: {**ARTICLE, "business_id": BID, "author": None},
    )
    content, _ = svc.export(db, AID, "markdown")
    assert 'author: "Acme Editorial"' in content
    # Per-article override wins over the brand default.
    monkeypatch.setattr(
        svc.gen_svc, "get_article",
        lambda d, aid: {**ARTICLE, "business_id": BID, "author": "Jane Doe"},
    )
    content2, _ = svc.export(db, AID, "markdown")
    assert 'author: "Jane Doe"' in content2


# --- webhook SSRF guard ---
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "https://10.0.0.1/hook",
        "https://192.168.1.5/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://100.64.0.1/hook",  # CGNAT (100.64/10) — only is_global rejects this
        "http://api.internal/hook",  # internal name short-circuit
        "ftp://example.com/x",
        "https://",
    ],
)
def test_validate_webhook_url_blocks_private_and_bad_scheme(url):
    with pytest.raises(ValueError):
        svc.validate_webhook_url(url)


def test_validate_webhook_url_allows_public():
    svc.validate_webhook_url("https://8.8.8.8/hook")  # public IP — no raise


# --- service ---
async def test_publish_export_marks_published(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": "p1", "article_id": AID, "target_type": "export",
        "status": "success", "created_at": "2026-06-20T00:00:00Z",
    }
    monkeypatch.setattr(svc.gen_svc, "get_article", lambda db, aid: ARTICLE)
    pub = await svc.publish(db, AID, target_type="export", public_base_url="http://x")
    assert pub["status"] == "success"
    sql = " ".join(c.args[0].lower() for c in db.execute.call_args_list)
    assert "status = 'published'" in sql


async def test_publish_webhook_delivery_failure_does_not_go_live(monkeypatch):
    """A webhook that fails to DELIVER records 'failed' WITHOUT flipping the article to
    'published' — otherwise it's publicly crawlable at /p/{id} while marked failed."""
    db = MagicMock()
    db.fetch_one.return_value = {
        "id": "p1", "article_id": AID, "target_type": "webhook",
        "status": "failed", "created_at": "2026-06-20T00:00:00Z",
    }
    monkeypatch.setattr(
        svc.gen_svc, "get_article", lambda db, aid: {**ARTICLE, "business_id": BID}
    )
    monkeypatch.setattr(svc, "validate_webhook_url", lambda u: None)  # URL passes
    monkeypatch.setattr(svc.linking, "resolve_links", lambda *a, **k: "# body")
    monkeypatch.setattr(svc.linking, "canonical_url", lambda *a, **k: None)

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _Boom())
    pub = await svc.publish(
        db, AID, target_type="webhook",
        config={"url": "https://example.com/hook"}, public_base_url="http://x",
    )
    assert pub["status"] == "failed"
    # _go_live() (the only db.execute) must never have run on the failure path.
    update_sql = " ".join(c.args[0].lower() for c in db.execute.call_args_list)
    assert "status = 'published'" not in update_sql


def _brand_db() -> MagicMock:
    """db whose fetch_one yields an article in the caller's org (passes the
    gen_svc.get_article lookup + assert_brand_access in the publish routes)."""
    db = MagicMock()
    db.fetch_one.return_value = {**ARTICLE, "business_id": BID, "org_id": UUID(ADMIN_ORG)}
    return db


# --- routes ---
def _client(db, auth: bool = True) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    if auth:
        with_auth(app)
    return TestClient(app)


def test_public_article_is_unauthenticated_and_404s_when_absent(monkeypatch):
    monkeypatch.setattr(svc, "get_published", lambda db, aid: None)
    resp = _client(MagicMock(), auth=False).get(f"/api/public/articles/{AID}")
    assert resp.status_code == 404  # not 401 — the route is public


def test_public_article_renders_fresh_from_markdown(monkeypatch):
    # The route renders content_html from content_md at read time (never stale).
    monkeypatch.setattr(
        svc, "get_published",
        lambda db, aid: {
            "id": AID, "business_id": AID, "title": "T", "slug": "t",
            "meta_title": "T", "meta_description": "d",
            "content_md": "# Live Heading\n\nbody",
            "json_ld": {"@type": "BlogPosting"}, "updated_at": "2026-06-20T00:00:00Z",
        },
    )
    # The public view resolves the brand + first-publication date; with fetch_one → None
    # there's no brand (author None) and no prior publication (published_at falls back to
    # updated_at). content_md stays out of the response; content_html is rendered fresh.
    db = MagicMock()
    db.fetch_one.return_value = None
    resp = _client(db, auth=False).get(f"/api/public/articles/{AID}")
    assert resp.status_code == 200
    data = resp.json()
    assert "<h1>Live Heading</h1>" in data["content_html"]
    # meta_description surfaces as the guaranteed social `description`.
    assert data["description"] == "d"
    assert data["published_at"] == "2026-06-20T00:00:00Z"


def test_excerpt_strips_markdown_and_truncates():
    from rankforge_backend.services.publishing import _excerpt

    md = "# Heading\n\nRead the [full guide](https://x.io) here before the rest of the body."
    ex = _excerpt(md, limit=40)
    assert "Heading" not in ex  # leading H1 (the title) removed
    assert "full guide" in ex  # link anchor text kept
    assert "http" not in ex and "](" not in ex  # URL + link syntax dropped
    assert ex.endswith("…")  # truncated past the limit


def test_publish_requires_editor(monkeypatch):
    from rankforge_backend.models.profile import CurrentUser

    async def fake_publish(db, aid, **k):
        return None  # shouldn't be reached

    monkeypatch.setattr(svc, "publish", fake_publish)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    with_auth(app, CurrentUser(id=AID, role="writer", org_id=ADMIN_ORG))
    resp = TestClient(app).post(
        f"/api/articles/{AID}/publish", json={"target_type": "export"}
    )
    assert resp.status_code == 403


CID = "88888888-8888-8888-8888-888888888888"


def test_unpublish_pillar_vacates_slot_but_keeps_membership(monkeypatch):
    db = MagicMock()
    conn = MagicMock()
    db.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(
        svc.gen_svc, "get_article",
        MagicMock(side_effect=[
            {"id": AID, "business_id": BID, "cluster_id": CID, "cluster_role": "pillar"},
            {"id": AID, "status": "draft", "cluster_id": CID, "cluster_role": "member"},
        ]),
    )
    out = svc.unpublish(db, AID)
    assert out["status"] == "draft"
    # one transaction: vacate the cluster's pillar slot, demote to member (keep
    # membership so republish rejoins), and record the unpublish audit row.
    assert conn.execute.call_count == 3
    sql = " ".join(c.args[0].lower() for c in conn.execute.call_args_list)
    assert "content_clusters set pillar_article_id = null" in sql
    assert "status = 'draft'" in sql and "cluster_role = 'member'" in sql
    # membership is NOT dropped — no cluster_id = null on the article update
    assert "cluster_id = null" not in sql
    assert "insert into public.publications" in sql and "'unpublished'" in sql


def test_unpublish_member_keeps_membership_without_touching_pillar(monkeypatch):
    db = MagicMock()
    conn = MagicMock()
    db.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(
        svc.gen_svc, "get_article",
        MagicMock(side_effect=[
            {"id": AID, "business_id": BID, "cluster_id": CID, "cluster_role": "member"},
            {"id": AID, "status": "draft", "cluster_id": CID, "cluster_role": "member"},
        ]),
    )
    svc.unpublish(db, AID)
    # no pillar clear for a member: just the draft revert + the audit row.
    assert conn.execute.call_count == 2
    sql = " ".join(c.args[0].lower() for c in conn.execute.call_args_list)
    assert "content_clusters set pillar_article_id = null" not in sql
    assert "cluster_id = null" not in sql  # membership retained
    assert "insert into public.publications" in sql and "'unpublished'" in sql


def test_unpublish_route_requires_editor():
    from rankforge_backend.models.profile import CurrentUser

    app = create_app()
    app.dependency_overrides[get_db] = lambda: _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    with_auth(app, CurrentUser(id=AID, role="writer", org_id=ADMIN_ORG))
    resp = TestClient(app).post(f"/api/articles/{AID}/unpublish")
    assert resp.status_code == 403


def test_publish_route(monkeypatch):
    async def fake_publish(db, aid, **k):
        return {
            "id": "66666666-6666-6666-6666-666666666666",
            "article_id": AID, "target_type": "export",
            "status": "success", "created_at": "2026-06-20T00:00:00Z",
        }

    monkeypatch.setattr(svc, "publish", fake_publish)
    resp = _client(_brand_db()).post(
        f"/api/articles/{AID}/publish", json={"target_type": "export"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_export_route_sets_slug_mdx_filename(monkeypatch):
    monkeypatch.setattr(svc, "export", lambda db, aid, fmt: ("# md", "text/markdown"))
    resp = _client(_brand_db()).get(f"/api/articles/{AID}/export?format=markdown")
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert 'filename="title.mdx"' in cd  # <slug>.mdx
