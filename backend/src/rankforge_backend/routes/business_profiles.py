"""business_profiles CRUD endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import Database
from ..models.business import (
    BusinessProfile,
    BusinessProfileCreate,
    BusinessProfileUpdate,
)
from ..services import business_profiles as svc
from .deps import get_db, get_powabase  # re-exported for callers/tests

__all__ = ["router", "get_db", "get_powabase"]

router = APIRouter(prefix="/api/business-profiles", tags=["business-profiles"])


@router.get("", response_model=list[BusinessProfile])
def list_business_profiles(db: Database = Depends(get_db)):
    return svc.list_profiles(db)


@router.post("", response_model=BusinessProfile, status_code=status.HTTP_201_CREATED)
def create_business_profile(
    payload: BusinessProfileCreate, db: Database = Depends(get_db)
):
    if svc.name_exists(db, payload.name):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'A brand named "{payload.name}" already exists',
        )
    return svc.create_profile(db, payload)


@router.get("/{profile_id}", response_model=BusinessProfile)
def get_business_profile(profile_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_profile(db, profile_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    return row


@router.patch("/{profile_id}", response_model=BusinessProfile)
def update_business_profile(
    profile_id: UUID, payload: BusinessProfileUpdate, db: Database = Depends(get_db)
):
    row = svc.update_profile(db, profile_id, payload)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    return row


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business_profile(
    profile_id: UUID, request: Request, db: Database = Depends(get_db)
):
    brand = svc.get_profile(db, profile_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    # Best-effort: delete the brand's grounding KB so it doesn't dangle in Powabase.
    pb = request.app.state.powabase
    if pb and brand.get("brand_kb_id"):
        try:
            await pb.delete_kb(brand["brand_kb_id"])
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            pass
    svc.delete_profile(db, profile_id)
