"""Content-scout endpoints (M5) — config, manual run, and the opportunity inbox."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user, require_editor
from ..db import Database
from ..models.scout import Opportunity, ScoutConfig, ScoutConfigUpdate, ScoutRun
from ..powabase import PowabaseClient
from ..services import scouts as svc
from ..tasks import spawn
from .deps import get_db, get_powabase

router = APIRouter(
    prefix="/api",
    tags=["scouts"],
    dependencies=[Depends(get_current_user)],
)


# --- config ---
@router.get("/scouts/config", response_model=ScoutConfig)
def get_scout_config(business_id: UUID, db: Database = Depends(get_db)):
    # Read-only: don't INSERT on a GET. The row is created lazily on PUT.
    return svc.get_config(db, business_id) or svc.default_config(business_id)


@router.put("/scouts/config", response_model=ScoutConfig)
def update_scout_config(
    business_id: UUID,
    payload: ScoutConfigUpdate,
    db: Database = Depends(get_db),
    _: object = Depends(require_editor),
):
    return svc.update_config(
        db, business_id, payload.model_dump(exclude_unset=True)
    )


# --- runs ---
@router.post("/scouts/run", status_code=status.HTTP_202_ACCEPTED)
async def run_scout_now(
    business_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    _: object = Depends(require_editor),
):
    """Trigger a scout immediately. Poll GET /api/scouts/runs + /api/opportunities."""
    spawn(svc.run_scout(pb, db, business_id=business_id, trigger="manual"))
    return {"status": "started", "business_id": str(business_id)}


@router.get("/scouts/runs", response_model=list[ScoutRun])
def list_scout_runs(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_runs(db, business_id)


# --- opportunity inbox ---
@router.get("/opportunities", response_model=list[Opportunity])
def list_opportunities(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_opportunities(db, business_id)


@router.post("/opportunities/{opp_id}/draft", response_model=Opportunity)
async def draft_opportunity(
    opp_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
):
    """Promote one opportunity through the generation pipeline (staged in_review)."""
    opp = svc.get_opportunity(db, opp_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    if opp["status"] in ("drafting", "drafted"):
        return opp
    queued = svc.set_opportunity_status(db, opp_id, "queued")
    spawn(svc.auto_draft(pb, db, opp))
    return queued


@router.post("/opportunities/{opp_id}/dismiss", response_model=Opportunity)
def dismiss_opportunity(opp_id: UUID, db: Database = Depends(get_db)):
    row = svc.set_opportunity_status(db, opp_id, "dismissed")
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    return row
