"""Centralized scraped-sources library."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import assert_brand_access, get_current_user
from ..db import Database
from ..models.profile import CurrentUser
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
def list_brand_sources(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return svc.list_brand_sources(db, business_id)


@router.get("/{source_id}/markdown")
async def get_source_markdown(
    source_id: str,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    if not svc.source_in_org(db, source_id, user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    try:
        md = await pb.get_source_markdown(source_id)
    except PowabaseError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return {"source_id": source_id, "markdown": md}
