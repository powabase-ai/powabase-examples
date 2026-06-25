"""Content-cluster engine — assignment orchestration + reads/writes (hermetic)."""

from unittest.mock import AsyncMock, MagicMock

from rankforge_backend.services import clusters

BID = "11111111-1111-1111-1111-111111111111"
CID = "22222222-2222-2222-2222-222222222222"
NEWID = "33333333-3333-3333-3333-333333333333"
AID = "44444444-4444-4444-4444-444444444444"


# --- assignment engine ---
async def test_assign_joins_existing_cluster(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(clusters.brands, "get_profile", lambda d, bid: {"name": "Acme"})
    monkeypatch.setattr(clusters, "ensure_cluster_kb", AsyncMock(return_value="kb1"))
    monkeypatch.setattr(
        clusters, "_retrieve_candidates",
        AsyncMock(return_value=[{"id": CID, "label": "X", "theme": "t"}]),
    )
    monkeypatch.setattr(
        clusters, "_run_agent",
        AsyncMock(return_value={"decision": "join", "cluster_id": CID}),
    )
    cid, role = await clusters.assign(MagicMock(), db, BID, title="a subtopic")
    assert (cid, role) == (CID, "member")
    db.fetch_one.assert_not_called()  # joining → no cluster insert


async def test_assign_founds_new_cluster_and_indexes_it(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {"id": NEWID, "label": "New", "index_doc_id": None}
    monkeypatch.setattr(clusters.brands, "get_profile", lambda d, bid: {"name": "Acme"})
    monkeypatch.setattr(clusters, "ensure_cluster_kb", AsyncMock(return_value="kb1"))
    monkeypatch.setattr(clusters, "_retrieve_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        clusters, "_run_agent",
        AsyncMock(return_value={"decision": "found", "label": "New Theme",
                               "theme": "scope of the theme"}),
    )
    idx = AsyncMock(return_value="doc1")
    monkeypatch.setattr(clusters, "_index_doc", idx)
    cid, role = await clusters.assign(MagicMock(), db, BID, title="a distinct theme")
    assert (cid, role) == (NEWID, "pillar")
    idx.assert_awaited_once()  # the cluster's index doc was created
    assert any(
        "set index_doc_id" in c.args[0] for c in db.execute.call_args_list
    )


async def test_assign_join_with_bad_id_falls_back_to_nearest(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(clusters.brands, "get_profile", lambda d, bid: {})
    monkeypatch.setattr(clusters, "ensure_cluster_kb", AsyncMock(return_value="kb1"))
    monkeypatch.setattr(
        clusters, "_retrieve_candidates",
        AsyncMock(return_value=[{"id": CID, "label": "X", "theme": "t"}]),
    )
    monkeypatch.setattr(
        clusters, "_run_agent",
        AsyncMock(return_value={"decision": "join", "cluster_id": "deadbeef"}),
    )
    cid, role = await clusters.assign(MagicMock(), db, BID, title="t")
    assert (cid, role) == (CID, "member")  # bad id → nearest candidate


async def test_retrieve_candidates_passes_all_when_few(monkeypatch):
    monkeypatch.setattr(clusters, "list_clusters", lambda d, bid: [{"id": 1}, {"id": 2}])
    client = MagicMock()
    client.search_kb = AsyncMock()
    out = await clusters._retrieve_candidates(client, MagicMock(), BID, "kb1", "q")
    assert len(out) == 2
    client.search_kb.assert_not_awaited()  # below threshold → skip retrieval


async def test_retrieve_candidates_searches_and_maps_when_many(monkeypatch):
    rows = [{"id": i, "index_doc_id": f"d{i}"} for i in range(10)]
    monkeypatch.setattr(clusters, "list_clusters", lambda d, bid: rows)
    client = MagicMock()
    client.search_kb = AsyncMock(
        return_value=[{"source_id": "d3"}, {"source_id": "d7"}]
    )
    out = await clusters._retrieve_candidates(client, MagicMock(), BID, "kb1", "q")
    assert [c["id"] for c in out] == [3, 7]  # hits mapped back to clusters, in order
    client.search_kb.assert_awaited_once()


# --- writes ---
def test_attach_article_claims_pillar_slot():
    db = MagicMock()
    clusters.attach_article(db, AID, CID, "pillar")
    qs = [c.args[0] for c in db.execute.call_args_list]
    assert any("set cluster_id = %s, cluster_role = %s" in q for q in qs)
    assert any(
        "pillar_article_id = %s" in q and "pillar_article_id is null" in q for q in qs
    )


def test_attach_article_member_does_not_claim_pillar():
    db = MagicMock()
    clusters.attach_article(db, AID, CID, "member")
    qs = [c.args[0] for c in db.execute.call_args_list]
    assert any("set cluster_id = %s, cluster_role = %s" in q for q in qs)
    assert not any("pillar_article_id" in q for q in qs)


def test_set_pillar_demotes_old_promotes_new_and_locks():
    db = MagicMock()
    db.fetch_one.side_effect = [{"id": CID}, {"id": CID, "pillar_locked": True}]
    out = clusters.set_pillar(db, BID, CID, AID)
    qs = [c.args[0] for c in db.execute.call_args_list]
    assert any("cluster_role = 'member'" in q for q in qs)  # demote previous pillar
    assert any("cluster_role = 'pillar'" in q for q in qs)  # promote the new one
    assert out["pillar_locked"] is True


def test_set_pillar_none_when_cluster_missing():
    db = MagicMock()
    db.fetch_one.return_value = None
    assert clusters.set_pillar(db, BID, CID, AID) is None
