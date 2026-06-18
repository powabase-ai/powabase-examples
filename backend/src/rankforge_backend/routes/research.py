"""Research (Stage A) endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import Database
from ..models.research import ResearchRun, ResearchRunCreate
from ..powabase import PowabaseClient
from ..services import research as svc
from .business_profiles import get_db

router = APIRouter(prefix="/api/research", tags=["research"])


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
    """Run Stage A research for a topic (synchronous — the agent run takes a while)."""
    try:
        return await svc.run_research(
            pb,
            db,
            business_id=payload.business_id,
            topic=payload.topic,
            locale=payload.locale,
            depth=payload.depth,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.get("/{run_id}", response_model=ResearchRun)
def get_research(run_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_run(db, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "research run not found")
    return row


@router.get("", response_model=list[ResearchRun])
def list_research(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_runs(db, business_id)
