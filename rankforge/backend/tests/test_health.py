"""Health endpoints — hermetic, no real DB/network.

Liveness is always-200. Readiness runs a real `select 1`; we mock at the
`Database` boundary (app.state.db) so the test stays hermetic.
"""

from fastapi.testclient import TestClient

from rankforge_backend.main import create_app


def test_health_ok():
    with TestClient(create_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_ok_when_db_query_succeeds():
    """DB configured + `select 1` works -> 200 ok."""

    class _FakeDB:
        async def afetch_one(self, query, params=None):
            assert "select 1" in query
            return {"ok": 1}

    app = create_app()
    with TestClient(app) as client:
        app.state.db = _FakeDB()
        resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


def test_ready_503_when_db_none():
    """No DB configured -> 503."""
    app = create_app()
    with TestClient(app) as client:
        app.state.db = None
        resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["db"] == "unavailable"


def test_ready_503_when_query_raises():
    """DB configured but the query fails -> 503."""

    class _BrokenDB:
        async def afetch_one(self, query, params=None):
            raise RuntimeError("pool exhausted / server closed connection")

    app = create_app()
    with TestClient(app) as client:
        app.state.db = _BrokenDB()
        resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["db"] == "unavailable"
