"""FastAPI application entrypoint.

Wires up the psycopg pool and the Powabase client at startup, exposes them on
`app.state`, and tears them down at shutdown.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from . import tasks
from .config import get_settings
from .db import Database
from .powabase import PowabaseClient
from .routes import (
    account,
    articles,
    brief,
    business_profiles,
    health,
    org,
    publish,
    research,
    scouts,
    sources,
    templates,
)
from .scheduler import ScoutScheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    db: Database | None = None
    if settings.powabase_database_url:
        db = Database(
            settings.powabase_database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            timeout=settings.db_pool_timeout,
        )
        db.open()
    app.state.db = db

    powabase: PowabaseClient | None = None
    if settings.powabase_base_url and settings.powabase_service_role_key:
        powabase = PowabaseClient(
            settings.powabase_base_url, settings.powabase_service_role_key
        )
    app.state.powabase = powabase

    # Autonomous content scouts — only when fully configured (never in tests).
    scheduler: ScoutScheduler | None = None
    if db is not None and powabase is not None:
        scheduler = ScoutScheduler(db, powabase)
        scheduler.start()
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown()  # stop new ticks
        await tasks.drain()  # let in-flight background work finish (bounded)
        if powabase is not None:
            await powabase.aclose()
        if db is not None:
            db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="RankForge API", version="0.0.1", lifespan=lifespan)

    # Never pair credentialed CORS with a wildcard origin — if CORS_ALLOW_ORIGINS is
    # misconfigured to "*", drop credentials rather than reflecting any origin.
    origins = settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Connection-pool exhaustion (too many concurrent requests for the pool) is a
    # transient overload, not a server bug — surface it as 503 with Retry-After so
    # clients back off instead of seeing an opaque 500.
    @app.exception_handler(PoolTimeout)
    async def _pool_timeout_handler(_request: Request, _exc: PoolTimeout):
        logging.getLogger("rankforge").warning("db pool exhausted (PoolTimeout)")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "service busy, retry shortly"},
            headers={"Retry-After": "5"},
        )

    app.include_router(health.router)
    app.include_router(account.router)
    app.include_router(org.router)
    app.include_router(business_profiles.router)
    app.include_router(research.router)
    app.include_router(brief.router)
    app.include_router(sources.router)
    app.include_router(articles.router)
    app.include_router(templates.router)
    app.include_router(scouts.router)
    app.include_router(publish.router)
    app.include_router(publish.public_router)
    return app


app = create_app()
