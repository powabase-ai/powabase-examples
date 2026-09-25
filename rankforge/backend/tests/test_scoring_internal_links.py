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


# --- round 2: branches of _norm_url / _is_internal_target / seo_brand_kwargs and
# the profile-driven limits that mutation testing found unpinned (S3/S5/S9) ---
def _il(seo):
    return next(s for s in seo["signals"] if s["key"] == "internal_links")


def test_norm_url_strips_space_scheme_www_query_and_case():
    assert scoring._norm_url("  https://WWW.PowaBase.ai/blog/a/?x=1#f ") == (
        "powabase.ai/blog/a/"
    )
    assert scoring._norm_url(" https://acme.com/a ") == "acme.com/a"


def test_uppercase_host_still_counts():
    assert _count("[a](https://PowaBase.AI/blog/a/)", _brand()) == 1


def test_hub_link_with_trailing_slash_counts_outside_the_blog_prefix():
    assert scoring._is_internal_target(
        "https://powabase.ai/vector-database/", ["powabase.ai/blog/"],
        {"powabase.ai/vector-database"},
    )
    assert _count("[hub](https://powabase.ai/vector-database/)", _brand()) == 1


def test_id_pattern_sets_the_blog_prefix():
    brand = _brand("https://acme.com/posts/{id}", "acme.com")
    assert scoring.seo_brand_kwargs(brand)["internal_prefixes"] == ["acme.com/posts/"]
    assert _count("[a](https://acme.com/posts/123)", brand) == 1


def test_relative_pattern_without_a_leading_slash_is_rooted():
    kw = scoring.seo_brand_kwargs(_brand("blog/{slug}", "acme.com"))
    assert kw["internal_prefixes"] == ["acme.com/blog/"]


def test_pattern_that_starts_with_the_token_uses_the_domain_root():
    kw = scoring.seo_brand_kwargs(_brand("{slug}", "acme.com"))
    assert kw["internal_prefixes"] == ["acme.com/"]


def test_no_domain_means_no_internal_hosts():
    kw = scoring.seo_brand_kwargs(_brand(pattern="", domain=""))
    assert kw["internal_hosts"] == set() and "internal_prefixes" not in kw
    assert _count("[rel](/pricing)", _brand(pattern="", domain="")) == 0


def test_internal_hosts_match_ignoring_www():
    s = scoring.score_seo("[x](https://acme.com/a)", "t", "m", {},
                          profile=BlogProfile.model_validate(_PROF),
                          internal_hosts={"www.acme.com"})
    assert _il(s)["explanation"].startswith("1 internal link")


def test_internal_links_fixes_below_at_and_above_the_band():
    prof = BlogProfile.model_validate({**_PROF, "links": {**_PROF["links"],
                                                          "min": 2, "max": 3}})

    def fixes(n: int) -> list[str]:
        md = " ".join(f"[a{i}](https://acme.com/a{i})" for i in range(n))
        s = scoring.score_seo(md, "t", "m", {}, profile=prof,
                              internal_hosts={"acme.com"})
        return _il(s)["fixes"]

    assert fixes(1) == ["Add 1 more contextual link(s) to the brand's own articles "
                        "or hub pages."]
    assert fixes(2) == [] and fixes(3) == []
    assert fixes(4) == ["Cut to at most 3 internal links."]


def test_meta_length_band_follows_the_profile_description_max():
    prof = BlogProfile.model_validate({**_PROF, "meta": {"title_max": 60,
                                                         "description_max": 130}})

    def meta_sig(n: int) -> dict:
        s = scoring.score_seo("body", "t", "m" * n, {}, profile=prof)
        return next(x for x in s["signals"] if x["key"] == "meta_length")

    assert meta_sig(115)["fixes"] == []  # 110-130 band: 120 is not the floor
    assert meta_sig(135)["fixes"] == ["Target 110–130 characters."]


# --- review r3 S8: the score falls off below `lo` (and above `hi`) ---
def test_internal_links_score_and_fix_around_the_band():
    prof = BlogProfile.model_validate({**_PROF, "links": {**_PROF["links"],
                                                          "min": 3, "max": 5}})

    def _il(n: int) -> dict:
        md = " ".join(f"[a{i}](https://powabase.ai/blog/a{i}/)" for i in range(n))
        s = scoring.score_seo(md, "t", "m", {}, profile=prof,
                              internal_hosts={"powabase.ai"})
        return next(x for x in s["signals"] if x["key"] == "internal_links")

    assert [_il(n)["score"] for n in (0, 1, 2, 3, 5, 6, 8)] == [
        0, 33, 67, 100, 100, 67, 0]
    assert _il(1)["fixes"] == [
        "Add 2 more contextual link(s) to the brand's own articles or hub pages."]
    assert _il(6)["fixes"] == ["Cut to at most 5 internal links."]
    assert _il(4)["fixes"] == []
