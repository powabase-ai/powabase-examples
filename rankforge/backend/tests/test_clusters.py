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


async def test_create_cluster_inserts_and_indexes(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {"id": NEWID, "label": "Billing", "index_doc_id": None}
    monkeypatch.setattr(clusters, "ensure_cluster_kb", AsyncMock(return_value="kb1"))
    idx = AsyncMock(return_value="doc1")
    monkeypatch.setattr(clusters, "_index_doc", idx)
    out = await clusters.create_cluster(
        MagicMock(), db, BID, label="Billing", theme="invoicing & payments"
    )
    # The row is inserted, then its one-doc index entry is built and stored back.
    assert "insert into public.content_clusters" in db.fetch_one.call_args.args[0]
    idx.assert_awaited_once()
    assert any("set index_doc_id" in c.args[0] for c in db.execute.call_args_list)
    assert out["index_doc_id"] == "doc1"


async def test_create_cluster_survives_index_doc_failure(monkeypatch):
    db = MagicMock()
    db.fetch_one.return_value = {"id": NEWID, "label": "Billing", "index_doc_id": None}
    monkeypatch.setattr(clusters, "ensure_cluster_kb", AsyncMock(return_value="kb1"))
    # Index-doc upload failed → cluster is still created (retrieval degrades gracefully).
    monkeypatch.setattr(clusters, "_index_doc", AsyncMock(return_value=None))
    out = await clusters.create_cluster(MagicMock(), db, BID, label="Billing")
    db.execute.assert_not_called()  # no index_doc_id to write back
    assert out["id"] == NEWID


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
    db.fetch_one.return_value = {"id": CID}  # slot was empty → claim succeeds
    clusters.attach_article(db, AID, CID, "pillar")
    # The pillar slot is claimed atomically (compare-and-set on pillar_article_id is null).
    claim = db.fetch_one.call_args.args[0]
    assert "pillar_article_id = %s" in claim and "pillar_article_id is null" in claim
    # The claim also accepts the slot already being held by THIS same article, and
    # threads the article id as the second param for that idempotent re-claim.
    assert "pillar_article_id = %s) returning id" in claim
    assert db.fetch_one.call_args.args[1] == (AID, CID, AID)
    # The article is then attached AS pillar (the claim won).
    role = db.execute.call_args.args[1][1]
    assert role == "pillar"


def test_attach_article_idempotent_reclaim_stays_pillar():
    db = MagicMock()
    # The slot is already held by THIS article → the OR-clause claim still matches,
    # so a re-attach of the current pillar stays pillar (not downgraded to member).
    db.fetch_one.return_value = {"id": CID}
    clusters.attach_article(db, AID, CID, "pillar")
    role = db.execute.call_args.args[1][1]
    assert role == "pillar"


def test_attach_article_demotes_to_member_when_pillar_slot_taken():
    db = MagicMock()
    db.fetch_one.return_value = None  # a concurrent draft already holds the slot
    clusters.attach_article(db, AID, CID, "pillar")
    # No phantom second pillar: the article is recorded as a member instead.
    role = db.execute.call_args.args[1][1]
    assert role == "member"


def test_attach_article_member_does_not_claim_pillar():
    db = MagicMock()
    clusters.attach_article(db, AID, CID, "member")
    db.fetch_one.assert_not_called()  # member path never touches the pillar slot
    role = db.execute.call_args.args[1][1]
    assert role == "member"


def test_set_pillar_demotes_old_promotes_new_and_locks():
    db = MagicMock()
    # cluster check, article-in-brand check, then the final update ... returning
    db.fetch_one.side_effect = [{"id": CID}, {"id": AID}, {"id": CID, "pillar_locked": True}]
    out = clusters.set_pillar(db, BID, CID, AID)
    qs = [c.args[0] for c in db.execute.call_args_list]
    assert any("cluster_role = 'member'" in q for q in qs)  # demote previous pillar
    assert any("cluster_role = 'pillar'" in q for q in qs)  # promote the new one
    assert out["pillar_locked"] is True


def test_move_article_rehomes_as_member_and_vacates_old_pillar():
    db = MagicMock()
    conn = MagicMock()
    db.connection.return_value.__enter__.return_value = conn
    # target-cluster-in-brand check, article-in-brand check
    db.fetch_one.side_effect = [{"id": NEWID}, {"id": AID}, {"id": NEWID}]
    out = clusters.move_article(db, BID, AID, NEWID)
    qs = [c.args[0] for c in conn.execute.call_args_list]
    # Any cluster this article anchored as pillar is vacated first...
    assert any("set pillar_article_id = null" in q for q in qs)
    # ...then the article is re-homed into the target cluster as a member.
    rehome = next(q for q in qs if "update public.articles set cluster_id" in q)
    assert "cluster_role = 'member'" in rehome
    assert out == {"id": NEWID}


def test_move_article_none_when_target_not_in_brand():
    db = MagicMock()
    db.fetch_one.return_value = None  # target cluster not in this brand
    assert clusters.move_article(db, BID, AID, NEWID) is None
    db.connection.assert_not_called()  # nothing mutated


def test_move_article_rejects_article_from_another_brand():
    db = MagicMock()
    db.fetch_one.side_effect = [{"id": NEWID}, None]  # target ok, article not in brand
    assert clusters.move_article(db, BID, AID, NEWID) is None
    db.connection.assert_not_called()


def test_set_pillar_none_when_cluster_missing():
    db = MagicMock()
    db.fetch_one.return_value = None
    assert clusters.set_pillar(db, BID, CID, AID) is None


def test_set_pillar_rejects_article_from_another_brand():
    db = MagicMock()
    db.fetch_one.side_effect = [{"id": CID}, None]  # cluster ok, article not in brand
    assert clusters.set_pillar(db, BID, CID, AID) is None
    db.execute.assert_not_called()  # nothing mutated


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
    assigned, remaining = await clusters.backfill(MagicMock(), db, BID)
    assert assigned == 1
    assert remaining is False  # the single row fit in one batch — nothing left
    attach.assert_called_once_with(db, AID, CID, "member")
    # The sweep is bounded: it asks for at most BACKFILL_BATCH+1 rows (one extra to
    # detect a remainder without a second query).
    sql, params = db.fetch_all.call_args.args
    assert "limit %s" in sql
    assert params[-1] == clusters.BACKFILL_BATCH + 1


async def test_backfill_reports_remaining_when_batch_full(monkeypatch):
    db = MagicMock()
    # One past the batch → the extra row signals more remain; only BATCH are processed.
    db.fetch_all.return_value = [
        {"id": AID, "title": f"T{i}", "keywords": []}
        for i in range(clusters.BACKFILL_BATCH + 1)
    ]
    monkeypatch.setattr(clusters, "assign", AsyncMock(return_value=(CID, "member")))
    monkeypatch.setattr(clusters, "attach_article", MagicMock())
    assigned, remaining = await clusters.backfill(MagicMock(), db, BID)
    assert assigned == clusters.BACKFILL_BATCH  # the extra row is NOT processed
    assert remaining is True


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


def test_move_member_route(monkeypatch):
    monkeypatch.setattr(
        clusters, "get_cluster", lambda d, cid: {"id": CID, "business_id": BID}
    )
    move = MagicMock(return_value={"id": CID})
    monkeypatch.setattr(clusters, "move_article", move)
    monkeypatch.setattr(clusters, "get_cluster_detail", lambda d, cid: CLUSTER_DETAIL)
    resp = _client().post(f"/api/clusters/{CID}/members", json={"article_id": AID})
    assert resp.status_code == 200
    assert resp.json()["label"] == "Auth"
    # The move is scoped to the guarded cluster's brand, moving the given article here.
    assert move.call_args.args[1:] == (BID, UUID(AID), UUID(CID))


def test_move_member_404_when_article_not_in_brand(monkeypatch):
    monkeypatch.setattr(
        clusters, "get_cluster", lambda d, cid: {"id": CID, "business_id": BID}
    )
    monkeypatch.setattr(clusters, "move_article", lambda *a: None)
    resp = _client().post(f"/api/clusters/{CID}/members", json={"article_id": AID})
    assert resp.status_code == 404


def test_move_member_requires_editor(monkeypatch):
    monkeypatch.setattr(
        clusters, "get_cluster", lambda d, cid: {"id": CID, "business_id": BID}
    )
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/clusters/{CID}/members", json={"article_id": AID}
    )
    assert resp.status_code == 403


def test_create_cluster_route(monkeypatch):
    monkeypatch.setattr(
        clusters, "create_cluster",
        AsyncMock(return_value={"id": NEWID, "business_id": BID, "label": "Billing",
                               "pillar_locked": False}),
    )
    resp = _client().post(
        f"/api/business-profiles/{BID}/clusters",
        json={"label": "Billing", "theme": "invoicing"},
    )
    assert resp.status_code == 201
    assert resp.json()["label"] == "Billing"
    assert resp.json()["member_count"] == 0  # a fresh cluster is empty


def test_create_cluster_requires_label(monkeypatch):
    resp = _client().post(
        f"/api/business-profiles/{BID}/clusters", json={"label": ""}
    )
    assert resp.status_code == 422  # empty label rejected by the schema


def test_create_cluster_requires_editor(monkeypatch):
    writer = CurrentUser(id=BID, role="writer", org_id=ADMIN_ORG)
    resp = _client(user=writer).post(
        f"/api/business-profiles/{BID}/clusters", json={"label": "Billing"}
    )
    assert resp.status_code == 403


def test_backfill_route_returns_count(monkeypatch):
    monkeypatch.setattr(clusters, "backfill", AsyncMock(return_value=(3, True)))
    resp = _client().post(f"/api/business-profiles/{BID}/clusters/backfill")
    assert resp.status_code == 200
    assert resp.json() == {"assigned": 3, "remaining": True}


async def test_delete_cluster_deindexes_and_clears_members(monkeypatch):
    db = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"id": CID}  # final delete row
    db.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(
        clusters, "get_cluster",
        lambda d, cid: {"id": CID, "business_id": BID, "index_doc_id": "doc1"},
    )
    monkeypatch.setattr(
        clusters.brands, "get_profile", lambda d, bid: {"cluster_kb_id": "kb1"}
    )
    monkeypatch.setattr(clusters, "indexed_source_id", AsyncMock(return_value="idx1"))
    monkeypatch.setattr(
        clusters.source_refs, "source_reference_count", lambda *a, **k: 0
    )
    client = MagicMock()
    client.remove_source_from_kb = AsyncMock()
    client.delete_source = AsyncMock()

    assert await clusters.delete_cluster(client, db, CID) is True
    client.remove_source_from_kb.assert_awaited_once_with("kb1", "idx1")
    client.delete_source.assert_awaited_once_with("doc1")
    # One transaction: clear article roles + opportunity roles + delete the row.
    assert conn.execute.call_count == 3


async def test_delete_cluster_keeps_shared_index_doc(monkeypatch):
    """If another workspace still references the cluster's index-doc Source (a content
    collision), de-index here but DON'T delete the project-wide Source."""
    db = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"id": CID}  # final delete row
    db.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(
        clusters, "get_cluster",
        lambda d, cid: {"id": CID, "business_id": BID, "index_doc_id": "doc1"},
    )
    monkeypatch.setattr(
        clusters.brands, "get_profile", lambda d, bid: {"cluster_kb_id": "kb1"}
    )
    monkeypatch.setattr(clusters, "indexed_source_id", AsyncMock(return_value="idx1"))
    # Source still referenced elsewhere (>0) → kept.
    monkeypatch.setattr(
        clusters.source_refs, "source_reference_count", lambda *a, **k: 1
    )
    client = MagicMock()
    client.remove_source_from_kb = AsyncMock()
    client.delete_source = AsyncMock()

    assert await clusters.delete_cluster(client, db, CID) is True
    client.remove_source_from_kb.assert_awaited_once_with("kb1", "idx1")
    client.delete_source.assert_not_awaited()  # shared — Source preserved


def test_delete_cluster_route(monkeypatch):
    monkeypatch.setattr(
        clusters, "get_cluster", lambda d, cid: {"id": CID, "business_id": BID}
    )
    monkeypatch.setattr(clusters, "delete_cluster", AsyncMock(return_value=True))
    resp = _client().delete(f"/api/clusters/{CID}")
    assert resp.status_code == 204


def test_analyze_gaps_route(monkeypatch):
    from rankforge_backend.services import scouts as scout_svc

    monkeypatch.setattr(
        clusters, "get_cluster", lambda d, cid: {"id": CID, "business_id": BID}
    )
    monkeypatch.setattr(scout_svc, "analyze_cluster_gaps", lambda d, bid, cid: 3)
    resp = _client().post(f"/api/clusters/{CID}/analyze-gaps")
    assert resp.status_code == 200
    assert resp.json()["created"] == 3
