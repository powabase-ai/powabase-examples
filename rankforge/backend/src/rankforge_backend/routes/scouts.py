"""Content-scout endpoints (M5) — config, manual run, and the opportunity inbox."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import (
    assert_brand_access,
    get_current_user,
    require_editor,
)
from ..db import Database
from ..models.profile import CurrentUser
from ..models.scout import Opportunity, ScoutConfig, ScoutConfigUpdate, ScoutRun
from ..powabase import PowabaseClient
from ..ratelimit import rate_limit
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
def get_scout_config(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    # Read-only: don't INSERT on a GET. The row is created lazily on PUT.
    return svc.get_config(db, business_id) or svc.default_config(business_id)


@router.put("/scouts/config", response_model=ScoutConfig)
def update_scout_config(
    business_id: UUID,
    payload: ScoutConfigUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    assert_brand_access(db, business_id, user)
    return svc.update_config(
        db, business_id, payload.model_dump(exclude_unset=True)
    )


# --- runs ---
@router.post(
    "/scouts/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("scout:run"))],
)
async def run_scout_now(
    business_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Trigger a scout immediately. Poll GET /api/scouts/runs + /api/opportunities."""
    assert_brand_access(db, business_id, user)
    spawn(svc.run_scout(pb, db, business_id=business_id, trigger="manual"))
    return {"status": "started", "business_id": str(business_id)}


@router.get("/scouts/runs", response_model=list[ScoutRun])
def list_scout_runs(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return svc.list_runs(db, business_id)


# --- opportunity inbox ---
@router.get("/opportunities", response_model=list[Opportunity])
def list_opportunities(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return svc.list_opportunities(db, business_id)


@router.post(
    "/opportunities/{opp_id}/draft",
    response_model=Opportunity,
    dependencies=[Depends(rate_limit("opportunity:draft"))],
)
async def draft_opportunity(
    opp_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Promote one opportunity through the generation pipeline (staged in_review)."""
    opp = svc.get_opportunity(db, opp_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    assert_brand_access(db, opp["business_id"], user)
    # Atomically claim it; if already queued/drafting/drafted, don't launch a second
    # pipeline — return the current state.
    queued = svc.try_claim_opportunity(db, opp_id)
    if queued is None:
        return opp
    spawn(svc.auto_draft(pb, db, opp))
    return queued


@router.post("/opportunities/{opp_id}/dismiss", response_model=Opportunity)
def dismiss_opportunity(
    opp_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    opp = svc.get_opportunity(db, opp_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    assert_brand_access(db, opp["business_id"], user)
    return svc.set_opportunity_status(db, opp_id, "dismissed")


@router.post("/opportunities/{opp_id}/restore", response_model=Opportunity)
def restore_opportunity(
    opp_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Undo a dismissal (e.g. a misclick) — move the opportunity back to the inbox.
    Only a dismissed opportunity can be restored; one that's already been drafted
    stays as-is."""
    opp = svc.get_opportunity(db, opp_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    assert_brand_access(db, opp["business_id"], user)
    if opp["status"] != "dismissed":
        return opp
    return svc.set_opportunity_status(db, opp_id, "new")
