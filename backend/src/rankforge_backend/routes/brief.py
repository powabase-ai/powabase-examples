"""Brief (Stage B) endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import Database
from ..models.brief import Brief, BriefGenerate, BriefUpdate
from ..powabase import PowabaseClient
from ..services import brief as svc
from .deps import get_db, get_powabase

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


@router.post("", response_model=Brief, status_code=status.HTTP_201_CREATED)
async def generate_brief(
    payload: BriefGenerate,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
):
    try:
        return await svc.generate_brief(
            pb, db, research_run_id=payload.research_run_id,
            article_type=payload.article_type,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


@router.get("/{brief_id}", response_model=Brief)
def get_brief(brief_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_brief(db, brief_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brief not found")
    return row


@router.get("", response_model=list[Brief])
def list_briefs(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_briefs(db, business_id)


@router.patch("/{brief_id}", response_model=Brief)
def update_brief(
    brief_id: UUID, payload: BriefUpdate, db: Database = Depends(get_db)
):
    row = svc.update_brief(db, brief_id, payload)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brief not found")
    return row
