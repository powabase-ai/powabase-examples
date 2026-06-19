"""FastAPI application entrypoint.

Wires up the psycopg pool and the Powabase client at startup, exposes them on
`app.state`, and tears them down at shutdown.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import Database
from .powabase import PowabaseClient
from .routes import brief, business_profiles, health, research, sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    db: Database | None = None
    if settings.powabase_database_url:
        db = Database(
            settings.powabase_database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
        )
        db.open()
    app.state.db = db

    powabase: PowabaseClient | None = None
    if settings.powabase_base_url and settings.powabase_service_role_key:
        powabase = PowabaseClient(
            settings.powabase_base_url, settings.powabase_service_role_key
        )
    app.state.powabase = powabase

    try:
        yield
    finally:
        if powabase is not None:
            await powabase.aclose()
        if db is not None:
            db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="RankForge API", version="0.0.1", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(business_profiles.router)
    app.include_router(research.router)
    app.include_router(brief.router)
    app.include_router(sources.router)
    return app


app = create_app()
