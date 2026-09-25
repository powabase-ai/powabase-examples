"""internal_links counts links to the brand's blog and hub pages (by target, not
host), and the revise commit gate scores with the brand's profile (PR #26 review
round 1)."""

from unittest.mock import MagicMock

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import revise, scoring

_PROF = BlogProfile.model_validate({
    "categories": [{"key": "rag", "label": "R"}],
    "meta": {"title_max": 30},
    "links": {"min": 1, "max": 5, "hub_pages": [
        {"path": "/vector-database/", "title": "V", "topics": ["vector database"]},
    ]},
}).model_dump()


def _brand(pattern="https://powabase.ai/blog/{slug}", domain="powabase.ai"):
    return {"id": "b", "domain": domain, "url_pattern": pattern,
            "blog_profile": _PROF, "competitors": []}


def _count(md: str, brand) -> int:
    seo = scoring.score_seo(md, "t", "m", {}, **scoring.seo_brand_kwargs(brand))
    il = next(s for s in seo["signals"] if s["key"] == "internal_links")
    return int(il["explanation"].split()[0])


def test_counts_blog_articles_and_hub_pages_only():
    md = ("[a](https://powabase.ai/blog/a/) [b](https://www.powabase.ai/blog/b) "
          "[hub](https://powabase.ai/vector-database) "
          "[pricing](https://powabase.ai/pricing) [ext](https://other.com/blog/x/)")
    assert _count(md, _brand()) == 3


def test_blog_index_itself_is_not_an_article_link():
    assert _count("[blog](https://powabase.ai/blog/)", _brand()) == 0


def test_blog_on_a_subdomain_counts():
    brand = _brand("https://blog.acme.com/{slug}/", "acme.com")
    md = "[a](https://blog.acme.com/post-a/) [p](https://acme.com/pricing)"
    assert _count(md, brand) == 1


def test_relative_pattern_uses_the_brand_domain():
    brand = _brand("/blog/{slug}/", "https://www.powabase.ai")
    assert _count("[a](https://powabase.ai/blog/a/)", brand) == 1


def test_internal_hosts_kwarg_still_works():
    s = scoring.score_seo("[x](https://powabase.ai/pricing)", "t", "m", {},
                          profile=BlogProfile.model_validate(_PROF),
                          internal_hosts={"powabase.ai"})
    il = next(x for x in s["signals"] if x["key"] == "internal_links")
    assert il["explanation"].startswith("1 internal link")


def test_score_seo_for_counts_by_target(monkeypatch):
    from rankforge_backend.services import business_profiles as bp

    monkeypatch.setattr(bp, "get_profile", lambda d, b: _brand())
    art = {"business_id": "b", "title": "t", "brief_id": None}
    md = "[a](https://powabase.ai/blog/a/) [p](https://powabase.ai/pricing)"
    seo = scoring.score_seo_for(MagicMock(), art, md, brief={})
    il = next(s for s in seo["signals"] if s["key"] == "internal_links")
    assert il["explanation"].startswith("1 internal link")


# --- the revise commit gate scores with the profile (K5) ---
def test_det_scores_without_brand_kwargs_is_legacy():
    seo, _geo = revise._det_scores("# T\n\nbody", "t", None, {})
    assert "internal_links" not in {s["key"] for s in seo["signals"]}


def test_det_scores_with_brand_kwargs_uses_profile():
    kw = scoring.seo_brand_kwargs(_brand())
    seo, _geo = revise._det_scores("# T\n\nbody", "t" * 40, None, {}, seo_kwargs=kw)
    keys = {s["key"]: s for s in seo["signals"]}
    assert "internal_links" in keys
    assert "Target 30–30 characters." in keys["title_length"]["fixes"]  # title_max


def test_accept_revision_scores_resolved_bodies_with_the_profile(monkeypatch):
    """The commit gate resolves rf:article refs and passes the brand's scoring
    kwargs, so dropping an internal link shows up in internal_links."""
    from rankforge_backend.services import business_profiles as bp
    from rankforge_backend.services import linking

    monkeypatch.setattr(bp, "get_profile", lambda d, b: _brand())
    monkeypatch.setattr(linking, "resolve_links",
                        lambda d, b, md, **k: md.replace(
                            "rf:article/x", "https://powabase.ai/blog/x/"))
    seen = []
    real = revise._det_scores

    def spy(md, title, meta, brief, seo_kwargs=None):
        out = real(md, title, meta, brief, seo_kwargs=seo_kwargs)
        seen.append(next(s for s in out[0]["signals"] if s["key"] == "internal_links"))
        return out

    monkeypatch.setattr(revise, "_det_scores", spy)
    body = "# T\n\n" + "Plain words here. " * 60
    revise._accept_revision(body + "See [x](rf:article/x).", body + "See x.", "t",
                            None, {}, db=MagicMock(), article={"business_id": "b"})
    assert [s["explanation"].split()[0] for s in seen] == ["1", "0"]
