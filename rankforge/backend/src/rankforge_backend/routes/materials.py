"""Brand-materials endpoints (M6) — ingest the brand's own pages into a KB.

Async by design: the ingest crawls a sitemap and imports each page as a Powabase
Source (slow), so the POST spawns a background worker and returns 202; the GET
polls the brand's `materials_progress` + the source list.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import assert_brand_access, get_current_user, require_editor
from ..db import Database
from ..models.materials import MaterialsIngest, MaterialsView
from ..models.profile import CurrentUser
from ..powabase import PowabaseClient
from ..ratelimit import rate_limit
from ..services import brand_materials as svc
from ..services import business_profiles as brands
from ..tasks import spawn
from .deps import get_db, get_powabase

router = APIRouter(
    prefix="/api/business-profiles",
    tags=["materials"],
    dependencies=[Depends(get_current_user)],
)


@router.post(
    "/{business_id}/materials/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("materials:ingest"))],
)
async def ingest_materials(
    business_id: UUID,
    payload: MaterialsIngest,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Kick off a brand-materials build. Poll GET /{id}/materials for progress."""
    assert_brand_access(db, business_id, user)
    svc.add_urls(db, business_id, payload.urls, "manual")
    spawn(svc.ingest(pb, db, business_id, extra_urls=tuple(payload.urls)))
    return {"status": "started"}


@router.get("/{business_id}/materials", response_model=MaterialsView)
def get_materials(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    brand = brands.get_profile(db, business_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    return MaterialsView(
        sources=svc.list_sources(db, business_id),
        progress=brand.get("materials_progress") or {},
        kb_ready=bool(brand.get("materials_kb_id")),
    )


@router.delete(
    "/{business_id}/materials/{row_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_material(
    business_id: UUID,
    row_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    assert_brand_access(db, business_id, user)
    if not svc.remove_source(pb, db, business_id, row_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand source not found")
