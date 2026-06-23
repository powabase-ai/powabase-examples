"""Centralized scraped-sources library."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..db import Database
from ..models.research import BrandSource
from ..powabase import PowabaseClient, PowabaseError
from ..services import research as svc
from .deps import get_db, get_powabase

router = APIRouter(
    prefix="/api/sources",
    tags=["sources"],
    dependencies=[Depends(get_current_user)],
)


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
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return {"source_id": source_id, "markdown": md}
