"""Centralized scraped-sources library."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import Database
from ..models.research import BrandSource
from ..powabase import PowabaseClient, PowabaseError
from ..services import research as svc
from .business_profiles import get_db
from .research import get_powabase

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=list[BrandSource])
def list_brand_sources(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_brand_sources(db, business_id)


@router.get("/{source_id}/markdown")
async def get_source_markdown(
    source_id: str, pb: PowabaseClient = Depends(get_powabase)
):
    try:
        md = await pb.get_source_markdown(source_id)
    except PowabaseError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return {"source_id": source_id, "markdown": md}
