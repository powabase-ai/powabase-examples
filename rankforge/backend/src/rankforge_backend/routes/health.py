"""Health / readiness endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness — does not touch dependencies, always 200."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness — runs a real, cheap DB check (`select 1`).

    Returns 200 once the DB pool can actually serve a query; returns 503 when the
    DB is unconfigured or the query fails. The blocking pool work is offloaded to a
    thread so it doesn't stall the event loop. Powabase is intentionally NOT probed
    here — readiness stays DB-only so it isn't coupled to an external API's health.
    """
    db = request.app.state.db
    if db is None:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "db": "unavailable"},
        )
    try:
        await db.afetch_one("select 1 as ok")
    except Exception:  # noqa: BLE001 — any failure means not-ready
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "db": "unavailable"},
        )
    return JSONResponse(status_code=200, content={"status": "ok", "db": "ok"})
