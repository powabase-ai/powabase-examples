"""business_profiles CRUD endpoints."""

import time
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from ..auth import get_current_user, require_admin, require_editor
from ..db import Database
from ..models.business import (
    BusinessProfile,
    BusinessProfileCreate,
    BusinessProfileUpdate,
)
from ..models.profile import CurrentUser
from ..powabase import PowabaseClient
from ..services import business_profiles as svc
from ..services import source_refs
from .deps import get_db, get_powabase  # re-exported for callers/tests

# Raster only. SVG is deliberately excluded: it lands in a PUBLIC bucket with a stable,
# guessable URL, and an SVG opened directly (not via <img>) executes its embedded script
# in the storage origin — a stored-XSS vector that, given the shared .powabase.ai cookie
# scope, could reach session state. PNG/JPG/WebP cover every real logo.
_LOGO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
_MAX_LOGO_BYTES = 5 * 1024 * 1024

__all__ = ["router", "get_db", "get_powabase"]

router = APIRouter(
    prefix="/api/business-profiles",
    tags=["business-profiles"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[BusinessProfile])
def list_business_profiles(
    db: Database = Depends(get_db), user: CurrentUser = Depends(get_current_user)
):
    return svc.list_profiles(db, user.org_id)


@router.post("", response_model=BusinessProfile, status_code=status.HTTP_201_CREATED)
def create_business_profile(
    payload: BusinessProfileCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if svc.name_exists(db, payload.name, user.org_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'A brand named "{payload.name}" already exists',
        )
    return svc.create_profile(db, payload, user.org_id)


@router.get("/{profile_id}", response_model=BusinessProfile)
def get_business_profile(
    profile_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    row = svc.get_profile(db, profile_id)
    if row is None or row.get("org_id") != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    return row


@router.patch("/{profile_id}", response_model=BusinessProfile)
def update_business_profile(
    profile_id: UUID,
    payload: BusinessProfileUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    row = svc.update_profile(db, profile_id, payload, user.org_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    return row


@router.post("/{profile_id}/logo", response_model=BusinessProfile)
async def upload_business_logo(
    profile_id: UUID,
    file: UploadFile = File(...),
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Upload a brand logo to public storage and store its URL on the brand."""
    ext = _LOGO_EXT.get(file.content_type or "")
    if ext is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "logo must be a PNG, JPG, or WebP image"
        )
    content = await file.read()
    if not content or len(content) > _MAX_LOGO_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "logo must be non-empty and under 5 MB"
        )
    # Confirm the brand is in the caller's org before uploading anything to storage.
    brand = svc.get_profile(db, profile_id)
    if brand is None or brand.get("org_id") != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    # Namespace the object by org so a brand-id-guessable path in the shared public bucket
    # can't let an editor of another org overwrite (x-upsert) this org's logo.
    url = await pb.upload_public_object(
        "brand-logos",
        f"{user.org_id}/{profile_id}.{ext}",
        content,
        file.content_type or "image/png",
    )
    # Cache-bust: the object path is stable (brand id), so a fresh ?v refreshes the img.
    row = svc.update_profile(
        db,
        profile_id,
        BusinessProfileUpdate(logo_url=f"{url}?v={int(time.time())}"),
        user.org_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    return row


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business_profile(
    profile_id: UUID,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
):
    brand = svc.get_profile(db, profile_id)
    if brand is None or brand.get("org_id") != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    # Best-effort Powabase cleanup BEFORE the cascade drops the tracking rows:
    #  - delete the brand's KBs (grounding, materials, cluster index), and
    #  - delete the Sources only THIS brand uploaded (shared ones are left for the
    #    other workspaces). Otherwise the cascade removes the rows but leaks the
    #    project-wide Sources in Powabase.
    pb = request.app.state.powabase
    if pb:
        for kb_col in ("brand_kb_id", "materials_kb_id", "cluster_kb_id"):
            kb_id = brand.get(kb_col)
            if kb_id:
                try:
                    await pb.delete_kb(kb_id)
                except Exception:  # noqa: BLE001 — cleanup is best-effort
                    pass
        for sid in source_refs.brand_exclusive_source_ids(db, profile_id):
            try:
                await pb.delete_source(sid)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                pass
    svc.delete_profile(db, profile_id, user.org_id)
