"""Brand-materials endpoints (M6) — ingest the brand's own pages into a KB.

Async by design: the ingest crawls a sitemap and imports each page as a Powabase
Source (slow), so the POST spawns a background worker and returns 202; the GET
polls the brand's `materials_progress` + the source list.
"""

from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)

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


# Reject oversized uploads before buffering them into a Source (413).
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.post(
    "/{business_id}/materials/upload",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("materials:ingest"))],
)
async def upload_material(
    business_id: UUID,
    file: UploadFile = File(...),
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Upload one PDF/file as a brand-materials Source. Returns 202; poll GET."""
    assert_brand_access(db, business_id, user)
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "file too large (max 20 MB)",
        )
    spawn(
        svc.ingest_file(
            pb,
            db,
            business_id,
            file_name=file.filename or "upload",
            content=content,
            mime=file.content_type or "application/octet-stream",
        )
    )
    return {"status": "started"}


@router.get("/{business_id}/materials/{row_id}/content")
async def get_material_content(
    business_id: UUID,
    row_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Return a brand source's extracted markdown (for the inspect modal)."""
    assert_brand_access(db, business_id, user)
    md = await svc.source_content(pb, db, business_id, row_id)
    if md is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no extracted content for this source"
        )
    return {"content": md}


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
    if not await svc.remove_source(pb, db, business_id, row_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand source not found")
