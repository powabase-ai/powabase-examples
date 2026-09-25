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


# --- review r1 C1/K4 + K3: a re-draft versions the old body first; generation-time
# frontmatter flags land in the final progress ---
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

from rankforge_backend.services import frontmatter as _fm  # noqa: E402
from rankforge_backend.services import geo_optimize as _geo  # noqa: E402
from rankforge_backend.services import linkcheck as _lc  # noqa: E402
from rankforge_backend.services import linking as _linking  # noqa: E402
from rankforge_backend.services import quality as _quality  # noqa: E402
from rankforge_backend.services import revise as _revise  # noqa: E402
from rankforge_backend.services import scoring as _scoring  # noqa: E402


@pytest.fixture
def gen_env(monkeypatch):
    st = {"art": {"id": "A", "business_id": "B", "cluster_id": "C",
                  "content_md": "# Old\n\nhand-edited body", "title": "T"},
          "events": [], "updates": []}
    monkeypatch.setattr(gen, "get_article", lambda d, a: dict(st["art"]))

    def _upd(d, a, **f):
        st["events"].append(("update", sorted(f)))
        st["updates"].append(f)
        st["art"].update(f)

    monkeypatch.setattr(gen, "_update", _upd)
    monkeypatch.setattr(
        gen, "snapshot_version",
        lambda d, art: st["events"].append(("snapshot", art.get("content_md"))),
    )
    monkeypatch.setattr(
        gen.brands, "get_profile",
        lambda d, b: {"name": "B", "blog_profile": _BP.model_dump()},
    )
    monkeypatch.setattr(gen, "ensure_writer_agent", AsyncMock(return_value="w"))
    monkeypatch.setattr(gen, "_cluster_context", lambda *a: None)
    monkeypatch.setattr(gen, "_draft_article", AsyncMock(return_value="new body"))
    monkeypatch.setattr(_linking, "link_candidates", lambda *a: [])
    monkeypatch.setattr(_fm, "complete", AsyncMock(return_value=[]))
    for mod, name in ((_quality, "reflect"), (_geo, "optimize_and_store"),
                      (_scoring, "score_and_store"), (_revise, "refine"),
                      (_lc, "check_article")):
        monkeypatch.setattr(mod, name, AsyncMock())
    return st


async def _run_gen():
    await gen.run_generation_task(
        MagicMock(), MagicMock(), article_id="A",
        brief={"business_id": "B", "research_run_id": None, "topic": "t"},
    )


async def test_redraft_snapshots_existing_body_before_overwrite(gen_env):
    await _run_gen()
    ev = gen_env["events"]
    first_body_write = next(i for i, e in enumerate(ev)
                            if e[0] == "update" and "content_md" in e[1])
    assert ("snapshot", "# Old\n\nhand-edited body") in ev[:first_body_write]


async def test_first_draft_does_not_snapshot_empty_body(gen_env):
    gen_env["art"]["content_md"] = "  "
    await _run_gen()
    assert not any(e[0] == "snapshot" for e in gen_env["events"])


async def test_generation_stores_frontmatter_flags_in_progress(gen_env, monkeypatch):
    monkeypatch.setattr(_fm, "complete", AsyncMock(return_value=["faq has 1 item(s)"]))
    await _run_gen()
    final = gen_env["updates"][-1]
    assert final["generation_status"] == "done"
    assert final["progress"]["frontmatter_flags"] == ["faq has 1 item(s)"]


async def test_generation_flags_a_frontmatter_step_that_raised(gen_env, monkeypatch):
    """Review r3 N35: the step raising never fails generation, but it must say so."""
    monkeypatch.setattr(_fm, "complete", AsyncMock(side_effect=RuntimeError("x")))
    await _run_gen()
    final = gen_env["updates"][-1]
    assert final["generation_status"] == "done"
    assert final["progress"]["frontmatter_flags"] == [
        "the frontmatter step failed; run it again"
    ]


async def test_generation_progress_has_no_flags_when_clean(gen_env):
    await _run_gen()
    assert "frontmatter_flags" not in gen_env["updates"][-1]["progress"]


# --- review r2 N2 / K9: an invalid stored profile is surfaced, not silent ---
async def test_generation_flags_an_invalid_blog_profile(gen_env, monkeypatch):
    bad = {**_BP.model_dump(), "link": {"min": 1}}  # misspelled key
    monkeypatch.setattr(
        gen.brands, "get_profile", lambda d, b: {"name": "B", "blog_profile": bad}
    )
    fm_complete = AsyncMock(return_value=[])
    monkeypatch.setattr(_fm, "complete", fm_complete)
    await _run_gen()
    final = gen_env["updates"][-1]
    assert final["generation_status"] == "done"
    flags = final["progress"]["frontmatter_flags"]
    assert len(flags) == 1 and flags[0].startswith("blog profile is invalid: ")
    assert "link" in flags[0]
    fm_complete.assert_not_awaited()  # runs without the profile rules
