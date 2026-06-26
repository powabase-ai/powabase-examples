"""Shared-Source reference counting (hermetic — mocks the Database boundary)."""

from unittest.mock import MagicMock

from rankforge_backend.services import source_refs

SID = "src-123"
ROW = "11111111-1111-1111-1111-111111111111"


def test_empty_source_id_short_circuits():
    db = MagicMock()
    assert source_refs.source_reference_count(db, None) == 0
    assert source_refs.source_reference_count(db, "") == 0
    db.fetch_all.assert_not_called()


def test_counts_rows_across_all_registries():
    db = MagicMock()
    # Three matching rows anywhere ⇒ count 3 (Source is still needed).
    db.fetch_all.return_value = [{"?column?": 1}] * 3
    assert source_refs.source_reference_count(db, SID) == 3
    sql, params = db.fetch_all.call_args.args
    assert "public.brand_sources" in sql
    assert "public.research_sources" in sql
    assert "public.content_clusters" in sql
    assert "union all" in sql
    # source_id is bound once per registry, no exclusions added.
    assert params == (SID, SID, SID)


def test_brand_exclusive_source_ids_returns_unshared_sids():
    db = MagicMock()
    db.fetch_all.return_value = [{"sid": "a"}, {"sid": "b"}]
    out = source_refs.brand_exclusive_source_ids(db, ROW)
    assert out == ["a", "b"]
    sql, params = db.fetch_all.call_args.args
    # The query keeps only Sources NOT referenced by another business.
    assert "not exists" in sql.lower()
    assert "business_id <> %(bid)s" in sql
    assert params == {"bid": ROW}
