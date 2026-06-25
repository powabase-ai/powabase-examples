"""Pure generation helpers (no I/O)."""

from rankforge_backend.services import generation as gen


def test_brand_context_falls_back_to_audience_without_a_brand():
    out = gen._brand_context_block(None, "devs")
    assert out == "- Audience / brand: devs"
    # an empty/nameless brand also falls back rather than emitting a blank header
    assert gen._brand_context_block({"competitors": []}, None) == "- Audience / brand: n/a"


def test_brand_context_names_the_brand_and_its_competitors():
    brand = {
        "name": "Powabase",
        "description": "AI backend-as-a-service.",
        "competitors": [{"name": "Supabase", "domain": "supabase.com"},
                        {"domain": "firebase.google.com"}],
    }
    out = gen._brand_context_block(brand, "developers")
    assert "**Powabase**'s own blog" in out
    assert "Audience: developers" in out
    assert "What Powabase is: AI backend-as-a-service." in out
    # competitor names listed (domain used when a name is missing), with a do-not-promote
    assert "do NOT promote" in out
    assert "Supabase" in out and "firebase.google.com" in out
    # and the advocacy instruction is anchored to the brand name
    assert "never undersell Powabase" in out


def test_brand_context_omits_competitor_line_when_none():
    out = gen._brand_context_block({"name": "Acme"}, None)
    assert "**Acme**'s own blog" in out
    assert "Competitors" not in out
