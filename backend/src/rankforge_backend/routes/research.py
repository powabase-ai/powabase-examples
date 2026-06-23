"""Research (Stage A) endpoints — async (background run + status polling)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..db import Database
from ..models.research import ResearchRun, ResearchRunCreate, ResearchSource
from ..powabase import PowabaseClient
from ..services import research as svc
from ..tasks import spawn
from .deps import get_db, get_powabase

router = APIRouter(
    prefix="/api/research",
    tags=["research"],
    dependencies=[Depends(get_current_user)],
)


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
    spawn(
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
    return run


@router.get("", response_model=list[ResearchRun])
def list_research(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_runs(db, business_id)


@router.get("/{run_id}/sources", response_model=list[ResearchSource])
def list_run_sources(run_id: UUID, db: Database = Depends(get_db)):
    return svc.list_sources(db, run_id)


@router.get("/{run_id}", response_model=ResearchRun)
def get_research(run_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_run(db, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "research run not found")
    return row
