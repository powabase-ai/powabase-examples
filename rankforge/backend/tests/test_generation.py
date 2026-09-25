"""Pure generation helpers (no I/O)."""

from unittest.mock import MagicMock

from rankforge_backend.services import clusters as clusters_svc
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


# --- pillar-aware generation (cluster framing) ---
def test_cluster_block_member_links_up_to_the_pillar():
    out = gen._cluster_block({
        "role": "member", "cluster": "Auth",
        "pillar_title": "Auth Guide", "pillar_url": "https://b.com/auth",
    })
    assert "SUPPORTING" in out
    assert "Auth Guide" in out and "https://b.com/auth" in out
    assert "Link UP" in out


def test_cluster_block_pillar_is_told_to_be_comprehensive():
    out = gen._cluster_block({"role": "pillar", "cluster": "Auth",
                             "members": ["SSO setup", "MFA"]})
    assert "PILLAR" in out
    assert "SSO setup" in out and "MFA" in out


def test_cluster_block_empty_without_a_resolvable_cluster():
    assert gen._cluster_block(None) == ""
    # a member with no pillar URL yields no block (we won't invent a link target)
    assert gen._cluster_block({"role": "member", "cluster": "x"}) == ""


def test_cluster_context_member_resolves_its_pillar(monkeypatch):
    arts = {
        "A": {"id": "A", "cluster_id": "C", "cluster_role": "member"},
        "P": {"id": "P", "title": "Pillar", "slug": "p"},
    }
    monkeypatch.setattr(gen, "get_article", lambda d, aid: arts.get(str(aid)))
    monkeypatch.setattr(
        clusters_svc, "get_cluster",
        lambda d, cid: {"label": "Auth", "pillar_article_id": "P"},
    )
    out = gen._cluster_context(MagicMock(), "A", {"url_pattern": "https://b.com/{slug}"})
    assert out["role"] == "member"
    assert out["pillar_title"] == "Pillar"
    assert out["pillar_url"] == "https://b.com/p"


def test_cluster_context_pillar_lists_members(monkeypatch):
    db = MagicMock()
    db.fetch_all.return_value = [{"title": "SSO"}, {"title": "MFA"}]
    monkeypatch.setattr(
        gen, "get_article",
        lambda d, aid: {"id": aid, "cluster_id": "C", "cluster_role": "pillar"},
    )
    monkeypatch.setattr(
        clusters_svc, "get_cluster",
        lambda d, cid: {"label": "Auth", "pillar_article_id": "PID"},
    )
    out = gen._cluster_context(db, "A", None)
    assert out["role"] == "pillar"
    assert out["members"] == ["SSO", "MFA"]


def test_cluster_context_none_without_cluster(monkeypatch):
    monkeypatch.setattr(gen, "get_article", lambda d, aid: {"id": aid})
    assert gen._cluster_context(MagicMock(), "A", None) is None


# --- draft assembly (brand profile must not be clobbered by brand grounding) ---
async def test_draft_article_keeps_brand_profile_distinct_from_grounding(monkeypatch):
    """The `brand` PROFILE param and the materials-KB grounding (a list) must stay
    separate — a name collision once overwrote the profile with the list, so the
    brand-context line crashed with 'list has no attribute get'."""
    captured: dict[str, str] = {}

    async def fake_gather(client, kb_id, queries, **k):
        return [{"text": "a brand capability", "source_id": "s1"}] if kb_id else []

    monkeypatch.setattr(gen, "_gather_grounding", fake_gather)

    client = MagicMock()

    async def fake_collect(agent_id, msg):
        captured["msg"] = msg
        return {"content": "x" * 600}

    client.run_agent_collect = fake_collect

    body = await gen._draft_article(
        client, "agent-1",
        {"headings": ["H2: Setup"], "target_word_count": 1200, "suggested_title": "T"},
        {"topic": "auth", "primary_keyword": "auth",
         "secondary_keywords": [], "audience": "devs"},
        title="Auth Guide", kb_id="kb1", source_ids=None, url_by_source={},
        materials_kb_id="mkb", materials_url_by_source={},
        brand={"name": "Acme", "description": "AI BaaS."},
        cluster=None,
    )
    assert len(body) >= 500
    # The brand PROFILE drove the brand-context line (not the grounding list).
    assert "**Acme**'s own blog" in captured["msg"]


from rankforge_backend.models.blog import BlogProfile  # noqa: E402
from rankforge_backend.services import generation as _g  # noqa: E402

_BP = BlogProfile.model_validate({"categories": [{"key": "rag", "label": "R"}],
                                  "stance": "favor_brand"})


def test_writer_rules_profile_blocks():
    r = _g._writer_rules(_BP, "Powabase")
    assert "Do not write an FAQ" in r
    assert "Never state a Powabase gap" in r
    assert _g._writer_rules(None, "Powabase") == ""


def test_outline_drops_faq_heading_with_profile():
    heads = ["H2: Intro", "H2: FAQ", "H3: Is it safe?", "H2: Conclusion"]
    assert _g._outline_for(heads, _BP) == ["H2: Intro", "H2: Conclusion"]
    assert _g._outline_for(heads, None) == heads


def test_link_block_lists_targets():
    c = [{"title": "Vector DB", "target": "https://powabase.ai/vector-database/",
          "category": None}]
    b = _g._link_block(c, _BP)
    assert "https://powabase.ai/vector-database/" in b and "3-5" in b
    assert _g._link_block([], _BP) == ""
