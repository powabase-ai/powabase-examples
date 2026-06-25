"""Content-cluster engine — assignment orchestration + reads/writes + routes."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from conftest import ADMIN_ORG, with_auth
from fastapi.testclient import TestClient

from rankforge_backend.main import create_app
from rankforge_backend.models.profile import CurrentUser
from rankforge_backend.routes.deps import get_db, get_powabase
from rankforge_backend.services import clusters

BID = "11111111-1111-1111-1111-111111111111"
CID = "22222222-2222-2222-2222-222222222222"
NEWID = "33333333-3333-3333-3333-333333333333"
AID = "44444444-4444-4444-4444-444444444444"
CLUSTER_ROW = {
    "id": CID, "business_id": BID, "label": "Auth", "theme": "authentication",
    "pillar_article_id": None, "pillar_locked": False, "pillar_title": "Auth Guide",
    "member_count": 2,
}
CLUSTER_DETAIL = {**CLUSTER_ROW, "members": []}


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


def test_list_clusters_view_includes_pillar_and_count():
    db = MagicMock()
    db.fetch_all.return_value = [CLUSTER_ROW]
    out = clusters.list_clusters_view(db, BID)
    q = db.fetch_all.call_args.args[0]
    assert "member_count" in q and "pillar_title" in q
    assert out == [CLUSTER_ROW]


async def test_backfill_assigns_unclustered_articles(monkeypatch):
    db = MagicMock()
    db.fetch_all.return_value = [{"id": AID, "title": "T", "keywords": ["kw"]}]
    monkeypatch.setattr(clusters, "assign", AsyncMock(return_value=(CID, "member")))
    attach = MagicMock()
    monkeypatch.setattr(clusters, "attach_article", attach)
    n = await clusters.backfill(MagicMock(), db, BID)
    assert n == 1
    attach.assert_called_once_with(db, AID, CID, "member")


# --- routes (hermetic) ---
def _brand_db() -> MagicMock:
    db = MagicMock()
    db.fetch_one.return_value = {"org_id": UUID(ADMIN_ORG)}
    return db


def _client(db=None, user: CurrentUser | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else _brand_db()
    app.dependency_overrides[get_powabase] = lambda: MagicMock()
    return TestClient(with_auth(app, user) if user else with_auth(app))


def test_list_clusters_route(monkeypatch):
    monkeypatch.setattr(clusters, "list_clusters_view", lambda d, bid: [CLUSTER_ROW])
    resp = _client().get(f"/api/business-profiles/{BID}/clusters")
    assert resp.status_code == 200
    assert resp.json()[0]["label"] == "Auth"
    assert resp.json()[0]["member_count"] == 2


def test_get_cluster_route(monkeypatch):
    monkeypatch.setattr(
        clusters, "get_cluster", lambda d, cid: {"id": CID, "business_id": BID}
    )
    monkeypatch.setattr(clusters, "get_cluster_detail", lambda d, cid: CLUSTER_DETAIL)
    resp = _client().get(f"/api/clusters/{CID}")
    assert resp.status_code == 200
    assert resp.json()["label"] == "Auth"


def test_set_pillar_requires_editor(monkeypatch):
    monkeypatch.setattr(
        clusters, "get_cluster", lambda d, cid: {"id": CID, "business_id": BID}
    )
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/clusters/{CID}/pillar", json={"article_id": AID}
    )
    assert resp.status_code == 403


def test_backfill_route_is_202(monkeypatch):
    monkeypatch.setattr(clusters, "backfill", AsyncMock())
    resp = _client().post(f"/api/business-profiles/{BID}/clusters/backfill")
    assert resp.status_code == 202
