"""Health endpoints — hermetic, no DB/network (config left empty in env)."""

from fastapi.testclient import TestClient

from rankforge_backend.main import create_app


def test_health_ok():
    with TestClient(create_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_reports_unconfigured_deps(monkeypatch):
    # With no POWABASE_* env set, both deps report not-configured.
    monkeypatch.setenv("POWABASE_BASE_URL", "")
    monkeypatch.setenv("POWABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("POWABASE_DATABASE_URL", "")
    from rankforge_backend.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        resp = client.get("/health/ready")
    body = resp.json()
    assert resp.status_code == 200
    assert body["db_configured"] is False
    assert body["powabase_configured"] is False
