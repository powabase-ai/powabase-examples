"""Startup reconciliation of interrupted background work."""

from unittest.mock import MagicMock

from rankforge_backend.services.reconcile import reconcile_interrupted


def test_reconcile_resets_inflight_rows():
    db = MagicMock()
    db.fetch_one.return_value = {"n": 1}
    reconcile_interrupted(db)
    sqls = " ".join(c.args[0].lower() for c in db.fetch_one.call_args_list)
    # drafts back to the inbox, in-flight research + generation marked failed
    assert "update public.opportunities set status = 'new'" in sqls
    assert "update public.research_runs set status = 'failed'" in sqls
    assert "update public.articles set generation_status = 'failed'" in sqls
    assert "update public.business_profiles set materials_progress" in sqls
    # an orphaned scout run (status='running', no task behind it) is failed too, so the
    # UI stops disabling the run buttons / polling forever
    assert "update public.scout_runs set status = 'failed'" in sqls
    assert "where status = 'running'" in sqls
    assert "drafting" in sqls and "searching" in sqls
    assert db.fetch_one.call_count == 5


def test_reconcile_noop_when_nothing_inflight():
    db = MagicMock()
    db.fetch_one.return_value = {"n": 0}
    reconcile_interrupted(db)  # should not raise
    assert db.fetch_one.call_count == 5
