"""BlogProfile validation (pure Pydantic)."""

import pytest
from pydantic import ValidationError

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.models.business import BusinessProfileUpdate

CATS = [
    {"key": "rag", "label": "RAG", "description": "d", "technical": True},
    {"key": "enterprise", "label": "Ent", "description": "d", "technical": False},
]


def _profile(**over):
    return {"categories": CATS, **over}


def test_defaults_fill_in():
    p = BlogProfile.model_validate(_profile())
    assert p.summary.min_words == 40 and p.summary.max_words == 60
    assert p.faq.min == 3 and p.faq.max == 6
    assert p.meta.title_max == 60 and p.meta.description_max == 160
    assert p.links.min == 3 and p.links.max == 5 and p.links.trailing_slash
    assert p.stance == "neutral"


def test_rejects_duplicate_category_keys():
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(categories=[CATS[0], CATS[0]]))


def test_rejects_bad_category_key_shape():
    bad = [{**CATS[0], "key": "RAG Stuff"}]
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(categories=bad))


def test_rejects_min_over_max():
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(summary={"min_words": 70, "max_words": 60}))
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"min": 6, "max": 5}))


def test_hub_path_must_start_with_slash():
    hub = {"path": "vector-database/", "title": "t", "topics": ["vector database"]}
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"hub_pages": [hub]}))


def test_hub_topics_bounds():
    short = {"path": "/x/", "title": "t", "topics": ["db"]}
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"hub_pages": [short]}))
    none = {"path": "/x/", "title": "t", "topics": []}
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(links={"hub_pages": [none]}))


def test_requires_at_least_one_category():
    with pytest.raises(ValidationError):
        BlogProfile.model_validate(_profile(categories=[]))


def test_update_accepts_null_to_disable():
    u = BusinessProfileUpdate.model_validate({"blog_profile": None})
    assert "blog_profile" in u.model_fields_set and u.blog_profile is None
