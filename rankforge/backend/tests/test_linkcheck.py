"""Broken-link detection (M6 / Phase 12.3) — checker logic + route wiring."""

import socket
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import generation as gsvc
from rankforge_backend.services import linkcheck

BID = "11111111-1111-1111-1111-111111111111"
AID = "55555555-5555-5555-5555-555555555555"
FID = "88888888-8888-8888-8888-888888888888"
TID = "11111111-1111-1111-1111-111111111111"
ARTICLE = {"id": AID, "business_id": BID, "status": "published"}
BROKEN_ROW = {
    "id": FID, "business_id": BID, "article_id": AID, "url": "https://x.com/404",
    "anchor_text": "x", "kind": "external", "http_status": 404, "reason": "HTTP 404",
    "status": "open", "checked_at": "2026-06-20T00:00:00Z",
    "created_at": "2026-06-20T00:00:00Z",
}


# --- pure helpers ---
def test_extract_links_skips_fenced_code():
    md = "[a](https://a.com)\n```\n[c](https://c.com)\n```\n[d](/p/x)"
    urls = [u for _, u in linkcheck._extract_links(md)]
    assert "https://a.com" in urls
    assert "https://c.com" not in urls  # inside a code block
    assert "/p/x" in urls


def test_internal_reason_flags_unpublished_and_missing():
    db = MagicMock()
    db.fetch_one.return_value = {"status": "published"}
    assert linkcheck._internal_reason(db, "x") is None
    db.fetch_one.return_value = {"status": "draft"}
    assert linkcheck._internal_reason(db, "x")  # not published → broken
    db.fetch_one.return_value = None
    assert linkcheck._internal_reason(db, "x")  # gone → broken


async def test_check_article_flags_unpublished_internal_target(monkeypatch):
    db = MagicMock()
    # 1) internal target status lookup, 2) _record_broken insert ... returning
    db.fetch_one.side_effect = [{"status": "draft"}, BROKEN_ROW]
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"content_md": f"see [guide](/p/{TID})"},
    )
    out = await linkcheck.check_article(db, BID, AID)
    assert out == [BROKEN_ROW]


async def test_check_article_flags_external_4xx(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = BROKEN_ROW  # _record_broken

    async def fake_ext(client, url):
        return 404, "HTTP 404"

    monkeypatch.setattr(linkcheck, "_external_reason", fake_ext)
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"content_md": "see [x](https://example.com/missing)"},
    )
    out = await linkcheck.check_article(db, BID, AID)
    assert out == [BROKEN_ROW]


async def test_check_article_resolves_a_healthy_link(monkeypatch):
    db = MagicMock()

    async def fake_ext(client, url):
        return 200, None

    monkeypatch.setattr(linkcheck, "_external_reason", fake_ext)
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"content_md": "[x](https://ok.example.com)"},
    )
    out = await linkcheck.check_article(db, BID, AID)
    assert out == []
    assert any(
        "status = 'resolved'" in c[0][0] for c in db.execute.call_args_list
    )


def test_host_fetch_state_skips_internal_and_private_offline():
    # Internal-by-name and private/loopback IP literals: skip (no DNS, fully offline).
    assert linkcheck._host_fetch_state("localhost") == "skip"
    assert linkcheck._host_fetch_state("api.internal") == "skip"
    assert linkcheck._host_fetch_state("svc.local") == "skip"
    assert linkcheck._host_fetch_state("10.0.0.1") == "skip"  # private IP literal
    assert linkcheck._host_fetch_state("127.0.0.1") == "skip"  # loopback literal


def test_host_fetch_state_nxdomain_is_dead(monkeypatch):
    # A truly-nonexistent host (NXDOMAIN / no address) → 'dead' (a broken link).
    def _raise(host, port):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    assert linkcheck._host_fetch_state("made-up-docs.example") == "dead"


def test_host_fetch_state_transient_resolver_failure_is_skip(monkeypatch):
    # EAI_AGAIN is a transient resolver hiccup, NOT evidence the link is dead — a
    # momentary DNS failure must not mark a healthy external link broken.
    def _raise(host, port):
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    assert linkcheck._host_fetch_state("real-but-flaky.example") == "skip"


def test_host_fetch_state_public_ip_is_fetch(monkeypatch):
    # Resolves to a public IP → go verify the URL.
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("8.8.8.8", 0))],
    )
    assert linkcheck._host_fetch_state("dns.example") == "fetch"


async def test_external_reason_flags_unresolvable_host(monkeypatch):
    # A fabricated host that doesn't resolve is a broken link — not silently skipped.
    monkeypatch.setattr(linkcheck, "_host_fetch_state", lambda h: "dead")
    status, reason = await linkcheck._external_reason(
        MagicMock(), "https://made-up-docs.example/x"
    )
    assert status is None and reason and "resolve" in reason


class _FakeResp:
    def __init__(self, status: int):
        self.status_code = status


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient.head/get."""
    def __init__(self, head_status: int, get_status: int | None = None):
        self._head, self._get = head_status, get_status

    async def head(self, url):
        return _FakeResp(self._head)

    async def get(self, url):
        return _FakeResp(self._get if self._get is not None else self._head)


async def test_external_reason_403_bot_block_is_not_broken(monkeypatch):
    # The host resolves and the server answers — a 403 (bot-block/auth wall, e.g.
    # npmjs.com / nvd.nist.gov) is NOT a broken link, so it must not be flagged.
    monkeypatch.setattr(linkcheck, "_host_fetch_state", lambda h: "fetch")
    status, reason = await linkcheck._external_reason(
        _FakeClient(403, 403), "https://www.npmjs.com/package/x"
    )
    assert status == 403 and reason is None


async def test_external_reason_404_is_broken(monkeypatch):
    # A definitively-gone resource (404/410) IS a broken link.
    monkeypatch.setattr(linkcheck, "_host_fetch_state", lambda h: "fetch")
    status, reason = await linkcheck._external_reason(
        _FakeClient(404), "https://github.com/x/blob/main/MISSING.md"
    )
    assert status == 404 and reason == "HTTP 404"


# --- remedy: unlink / remove a broken link ---
def _capture_update(monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(
        linkcheck.gen_svc, "update_article",
        lambda d, aid, fields: captured.update(fields) or {"id": aid},
    )


async def test_remove_link_unlink_keeps_anchor_text(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {**BROKEN_ROW, "url": "https://x.com/404"}
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"id": aid, "content_md": "see [the guide](https://x.com/404) now"},
    )
    captured: dict = {}
    _capture_update(monkeypatch, captured)
    # Unlink is instant + mechanical — it must NOT call the LLM editor.
    boom = AsyncMock(side_effect=AssertionError("unlink must not call the LLM"))
    monkeypatch.setattr(linkcheck, "ensure_link_editor_agent", boom)
    result = await linkcheck.remove_link(MagicMock(), db, BID, AID, FID, keep_text=True)
    assert result is not None
    assert captured["content_md"] == "see the guide now"  # words kept, link dropped
    assert any(
        "status = 'resolved'" in c.args[0] for c in db.execute.call_args_list
    )


async def test_remove_link_rephrases_the_sentence_via_llm(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {**BROKEN_ROW, "url": "https://x.com/404"}
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {
            "id": aid,
            "content_md": "Read [the guide](https://x.com/404) for the setup steps.",
        },
    )
    captured: dict = {}
    _capture_update(monkeypatch, captured)
    monkeypatch.setattr(
        linkcheck, "ensure_link_editor_agent", AsyncMock(return_value="ed")
    )

    async def fake_rephrase(client, agent_id, block, url):
        return "Read the setup steps."  # link gone, sentence mended

    monkeypatch.setattr(linkcheck, "_rephrase_block", fake_rephrase)
    await linkcheck.remove_link(MagicMock(), db, BID, AID, FID, keep_text=False)
    assert captured["content_md"] == "Read the setup steps."
    assert "x.com/404" not in captured["content_md"]


async def test_remove_link_falls_back_to_mechanical_when_llm_keeps_the_link(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {**BROKEN_ROW, "url": "https://x.com/404"}
    monkeypatch.setattr(
        linkcheck.gen_svc, "get_article",
        lambda d, aid: {"id": aid, "content_md": "see [the guide](https://x.com/404) now."},
    )
    captured: dict = {}
    _capture_update(monkeypatch, captured)
    monkeypatch.setattr(
        linkcheck, "ensure_link_editor_agent", AsyncMock(return_value="ed")
    )

    async def bad_rephrase(client, agent_id, block, url):
        return block  # model didn't drop the link → must fall back to mechanical strip

    monkeypatch.setattr(linkcheck, "_rephrase_block", bad_rephrase)
    await linkcheck.remove_link(MagicMock(), db, BID, AID, FID, keep_text=False)
    assert "x.com/404" not in captured["content_md"]  # link stripped regardless
    assert "  " not in captured["content_md"]  # spacing tidied


async def test_remove_link_none_when_finding_missing():
    db = MagicMock()
    db.fetch_one.return_value = None
    assert await linkcheck.remove_link(MagicMock(), db, BID, AID, FID, keep_text=True) is None
    db.execute.assert_not_called()  # nothing mutated


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


def test_list_broken_links_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(linkcheck, "list_findings", lambda d, aid: [BROKEN_ROW])
    resp = _client().get(f"/api/articles/{AID}/links/health")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == FID


def test_check_links_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)

    async def fake_check(d, bid, aid):
        return [BROKEN_ROW]

    monkeypatch.setattr(linkcheck, "check_article", fake_check)
    resp = _client().post(f"/api/articles/{AID}/links/check")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_check_links_requires_editor(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(f"/api/articles/{AID}/links/check")
    assert resp.status_code == 403


_FULL_ARTICLE = {
    "id": AID, "business_id": BID, "title": "T", "status": "draft",
    "generation_status": "done", "content_md": "see the guide now",
    "created_at": "2026-06-20T00:00:00Z", "updated_at": "2026-06-20T00:00:00Z",
}


def test_remove_broken_link_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(linkcheck, "remove_link", AsyncMock(return_value=_FULL_ARTICLE))
    resp = _client().post(
        f"/api/articles/{AID}/links/health/{FID}/remove", json={"keep_text": True}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == AID


def test_remove_broken_link_requires_editor(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/articles/{AID}/links/health/{FID}/remove", json={"keep_text": False}
    )
    assert resp.status_code == 403


def test_remove_broken_link_404_when_finding_missing(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(linkcheck, "remove_link", AsyncMock(return_value=None))
    resp = _client().post(
        f"/api/articles/{AID}/links/health/{FID}/remove", json={"keep_text": True}
    )
    assert resp.status_code == 404


def test_ignore_broken_link_route(monkeypatch):
    monkeypatch.setattr(gsvc, "get_article", lambda d, aid: ARTICLE)
    monkeypatch.setattr(
        linkcheck, "ignore_finding",
        lambda d, bid, fid: {**BROKEN_ROW, "status": "ignored"},
    )
    resp = _client().post(f"/api/articles/{AID}/links/health/{FID}/ignore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
