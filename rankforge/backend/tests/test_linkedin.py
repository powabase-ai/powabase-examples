"""LinkedIn post generator — models, prompt builder, CRUD service, routes (hermetic)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from rankforge_backend.models.linkedin import (
    ANGLE_SLUGS,
    LinkedInGenerate,
    LinkedInUpdate,
)
from rankforge_backend.services import linkedin_gen as li_gen
from rankforge_backend.services import linkedin_posts as li_svc


def test_angle_slugs_are_the_five_presets():
    assert ANGLE_SLUGS == ("key_insight", "lesson", "contrarian", "story", "stat")


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
