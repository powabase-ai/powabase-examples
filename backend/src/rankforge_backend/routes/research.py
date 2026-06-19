"""Research (Stage A) endpoints — async (background run + status polling)."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import Database
from ..models.research import ResearchRun, ResearchRunCreate, ResearchSource
from ..powabase import PowabaseClient, PowabaseError
from ..services import research as svc
from .business_profiles import get_db

router = APIRouter(prefix="/api/research", tags=["research"])

# keep references so background tasks aren't garbage-collected
_bg_tasks: set[asyncio.Task] = set()


def get_powabase(request: Request) -> PowabaseClient:
    pb = request.app.state.powabase
    if pb is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "powabase client not configured"
        )
    return pb


@router.post("", response_model=ResearchRun, status_code=status.HTTP_201_CREATED)
async def create_research(
    payload: ResearchRunCreate,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
):
    """Kick off research and return immediately. Poll GET /api/research/{id} for
    status (searching → scraping → done/failed) and results."""
    brand = svc.get_brand(db, payload.business_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    run = svc.create_research_run(
        db, business_id=payload.business_id, topic=payload.topic, locale=payload.locale
    )
    task = asyncio.create_task(
        svc.run_research_task(
            pb,
            db,
            run_id=run["id"],
            brand=brand,
            topic=payload.topic,
            locale=payload.locale,
            depth=payload.depth,
        )
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return run


@router.get("", response_model=list[ResearchRun])
def list_research(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_runs(db, business_id)


@router.get("/source/{source_id}/markdown")
async def get_source_markdown(
    source_id: str, pb: PowabaseClient = Depends(get_powabase)
):
    try:
        md = await pb.get_source_markdown(source_id)
    except PowabaseError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return {"source_id": source_id, "markdown": md}


@router.get("/{run_id}/sources", response_model=list[ResearchSource])
def list_run_sources(run_id: UUID, db: Database = Depends(get_db)):
    return svc.list_sources(db, run_id)


@router.get("/{run_id}", response_model=ResearchRun)
def get_research(run_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_run(db, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "research run not found")
    return row
