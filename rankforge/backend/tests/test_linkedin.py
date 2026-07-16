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
