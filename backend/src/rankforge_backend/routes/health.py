"""Health / readiness endpoints."""

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness — does not touch dependencies."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, object]:
    """Readiness — reports whether DB and Powabase client are configured.

    Kept dependency-light on purpose: it does not run a query or hit the network,
    so it stays fast and side-effect free. Deeper checks belong in a separate
    diagnostic endpoint.
    """
    return {
        "status": "ok",
        "db_configured": request.app.state.db is not None,
        "powabase_configured": request.app.state.powabase is not None,
    }
