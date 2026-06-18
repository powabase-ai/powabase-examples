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

router = APIRouter(prefix="/api/business-profiles", tags=["business-profiles"])


def get_db(request: Request) -> Database:
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "database not configured"
        )
    return db


@router.get("", response_model=list[BusinessProfile])
def list_business_profiles(db: Database = Depends(get_db)):
    return svc.list_profiles(db)


@router.post("", response_model=BusinessProfile, status_code=status.HTTP_201_CREATED)
def create_business_profile(
    payload: BusinessProfileCreate, db: Database = Depends(get_db)
):
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
def delete_business_profile(profile_id: UUID, db: Database = Depends(get_db)):
    if svc.get_profile(db, profile_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business profile not found")
    svc.delete_profile(db, profile_id)
