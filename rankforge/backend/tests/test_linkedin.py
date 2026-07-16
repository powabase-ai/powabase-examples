"""LinkedIn post generator — models, prompt builder, CRUD service, routes (hermetic)."""

import pytest
from pydantic import ValidationError

from rankforge_backend.models.linkedin import (
    ANGLE_SLUGS,
    LinkedInGenerate,
    LinkedInUpdate,
)


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


from unittest.mock import MagicMock

from rankforge_backend.services import linkedin_posts as li_svc

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
