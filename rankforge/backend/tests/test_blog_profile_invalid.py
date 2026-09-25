"""A present-but-invalid stored blog profile is loud, never a silent legacy fallback
(PR #26 review round 1, I8), plus the publish() export preflight at service level."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from rankforge_backend.models.blog import BlogProfile, HubPage
from rankforge_backend.services import blog_rules as br
from rankforge_backend.services import business_profiles as bp_svc
from rankforge_backend.services import publishing as svc

AID = "55555555-5555-5555-5555-555555555555"
BID = "11111111-1111-1111-1111-111111111111"
GOOD = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}]})
BAD = {"categories": "nope"}
_S = " ".join(["word"] * 44) + " end."
_FAQ = [{"q": f"Q{i}?", "a": "A."} for i in range(3)]
ARTICLE = {
    "id": AID, "business_id": BID, "title": "Title", "slug": "title",
    "meta_title": "Title", "meta_description": "A description.",
    "content_md": "Body.", "category": "rag", "summary": _S, "faq": _FAQ,
    "status": "approved",
}


# --- models: extra keys and hub paths ---
def test_blog_models_reject_unknown_keys():
    with pytest.raises(ValidationError):  # misspelled top-level key
        BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}],
                                    "link": {"min": 1}})
    with pytest.raises(ValidationError):  # misspelled nested key
        BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}],
                                    "links": {"minimum": 1}})


@pytest.mark.parametrize("path", ["//evil.com/x", "/\\evil.com", "https://evil.com/"])
def test_hub_path_rejects_protocol_relative_and_schemes(path):
    with pytest.raises(ValidationError):
        HubPage(path=path, title="t", topics=["vector database"])


def test_hub_path_accepts_a_site_path():
    assert HubPage(path="/vector-database/", title="t",
                   topics=["vector database"]).path == "/vector-database/"


def test_seed_profile_and_dump_round_trip_under_forbid():
    GOOD.model_validate(GOOD.model_dump())  # a dumped profile re-validates


# --- blog_rules ---
@pytest.fixture(autouse=True)
def _fresh_warning_memo():
    br._WARNED.clear()  # profile_of warns once per (brand, reason) per process
    yield
    br._WARNED.clear()


def test_profile_of_logs_a_warning_with_the_brand_id(caplog):
    with caplog.at_level(logging.WARNING):
        assert br.profile_of({"id": BID, "blog_profile": BAD}) is None
    assert any(BID in r.getMessage() for r in caplog.records)


def test_profile_of_warns_once_per_brand_and_reason(caplog):
    other = {"categories": [{"key": "rag", "label": "R"}], "links": {"min": -1}}
    with caplog.at_level(logging.WARNING, logger="rankforge.blog_rules"):
        for _ in range(3):  # e.g. one call per link on every render
            br.profile_of({"id": BID, "blog_profile": BAD})
        br.profile_of({"id": BID, "blog_profile": other})  # a new reason
        br.profile_of({"id": AID, "blog_profile": BAD})  # another brand
        br.profile_of({"id": AID, "blog_profile": BAD})
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 3
    assert sum(BID in m for m in msgs) == 2 and sum(AID in m for m in msgs) == 1


def test_invalid_profile_reason():
    assert br.invalid_profile_reason({"blog_profile": None}) is None
    assert br.invalid_profile_reason({"blog_profile": GOOD.model_dump()}) is None
    reason = br.invalid_profile_reason({"blog_profile": BAD})
    assert reason and "categories" in reason


# --- export / publish refuse an invalid stored profile ---
def _env(monkeypatch, brand, article=None):
    db = MagicMock()
    db.fetch_one.return_value = {"keywords": []}
    monkeypatch.setattr(svc.gen_svc, "get_article",
                        lambda _db, _id: dict(article or ARTICLE))
    monkeypatch.setattr(bp_svc, "get_profile", lambda _db, _id: brand)
    return db


def test_export_blocks_on_invalid_stored_profile(monkeypatch):
    db = _env(monkeypatch, {"id": BID, "name": "B", "blog_profile": BAD})
    with pytest.raises(svc.ExportBlocked) as e:
        svc.export(db, AID, "markdown")
    assert len(e.value.issues) == 1
    assert e.value.issues[0].startswith("blog profile is invalid: ")


async def test_publish_blocks_on_invalid_stored_profile(monkeypatch):
    db = _env(monkeypatch, {"id": BID, "name": "B", "blog_profile": BAD})
    with pytest.raises(svc.ExportBlocked) as e:
        await svc.publish(db, AID, target_type="export")
    assert e.value.issues[0].startswith("blog profile is invalid: ")
    db.execute.assert_not_called()


async def test_publish_preflight_blocks_before_any_side_effect(monkeypatch):
    """Service-level: a profile brand with an export issue raises ExportBlocked
    before the webhook is delivered, the status flips, or a publication is recorded."""
    db = _env(monkeypatch, {"id": BID, "name": "B", "blog_profile": GOOD.model_dump()},
              {**ARTICLE, "category": None})
    post = AsyncMock()
    monkeypatch.setattr(svc.httpx.AsyncClient, "post", post)
    record = MagicMock()
    monkeypatch.setattr(svc, "_record", record)
    with pytest.raises(svc.ExportBlocked) as e:
        await svc.publish(db, AID, target_type="webhook",
                          config={"url": "https://hooks.example.com/x"})
    assert any("category" in i for i in e.value.issues)
    post.assert_not_called()
    record.assert_not_called()
    db.execute.assert_not_called()  # status never flipped to published


async def test_publish_proceeds_when_the_article_passes(monkeypatch):
    db = _env(monkeypatch, {"id": BID, "name": "B", "blog_profile": GOOD.model_dump()})
    monkeypatch.setattr(svc, "_record", lambda *a, **k: {"status": k.get("status")})
    out = await svc.publish(db, AID, target_type="export")
    assert out == {"status": "success"}
    assert "status = 'published'" in db.execute.call_args.args[0]
