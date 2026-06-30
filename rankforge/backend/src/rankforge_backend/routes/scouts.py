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
from ..models.scout import (
    Opportunity,
    ScoutConfig,
    ScoutConfigUpdate,
    ScoutPlan,
    ScoutRun,
)
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


@router.get("/scouts/runs/{run_id}", response_model=ScoutRun)
def get_scout_run(
    run_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    run = svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scout run not found")
    assert_brand_access(db, run["business_id"], user)
    return run


# --- two-phase manual run: plan → review/edit → execute ---
@router.post(
    "/scouts/plan",
    response_model=ScoutRun,
    # Same expensive bucket as /scouts/run: planning spawns a planner LLM + web_search
    # and replaces the prior 'planned' row, so an unthrottled two-phase path would let
    # a user bypass the one-shot run's limiter and rack up unbounded Exa/LLM spend.
    dependencies=[Depends(rate_limit("scout:run"))],
)
async def plan_scout(
    business_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Start a Search Plan: research trending queries the user can review/edit before
    running. Returns the 'planned' run immediately; poll it until the plan appears."""
    assert_brand_access(db, business_id, user)
    run = svc.start_plan(db, business_id, trigger="manual")
    spawn(svc.generate_plan_for_run(pb, db, run["id"]))
    return run


@router.patch("/scouts/runs/{run_id}/plan", response_model=ScoutRun)
def update_scout_plan(
    run_id: UUID,
    payload: ScoutPlan,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    """Replace a planned run's Search Plan with the user's edits (only while planned)."""
    run = svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scout run not found")
    assert_brand_access(db, run["business_id"], user)
    updated = svc.update_plan(db, run_id, payload.model_dump())
    if updated is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this run has already started — its plan is locked"
        )
    return updated


@router.post(
    "/scouts/runs/{run_id}/execute",
    status_code=status.HTTP_202_ACCEPTED,
    # The planned->running CAS makes execute self-limiting per plan, but paired with
    # /scouts/plan a user could loop plan->execute freely — share the run bucket for
    # defense-in-depth (the full discover/store/auto-draft pipeline is the spend).
    dependencies=[Depends(rate_limit("scout:run"))],
)
async def execute_scout_run(
    run_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Run a planned (optionally edited) Search Plan. Poll the run / opportunities."""
    run = svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scout run not found")
    assert_brand_access(db, run["business_id"], user)
    if run["status"] != "planned":
        raise HTTPException(status.HTTP_409_CONFLICT, "run already started")
    spawn(svc.execute_run(pb, db, run_id))
    return {"status": "started", "run_id": str(run_id)}


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


@router.delete("/opportunities/{opp_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_opportunity(
    opp_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    """Permanently remove an opportunity from the inbox. (Dismiss keeps it for restore;
    this deletes it outright.)"""
    opp = svc.get_opportunity(db, opp_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    assert_brand_access(db, opp["business_id"], user)
    # A draft pipeline is mid-flight for this opportunity and is about to create an
    # article for it — deleting now would orphan that article. The pre-check is a
    # fast path; the authoritative guard is the conditional DELETE in the service
    # (see delete_opportunity), which also closes the TOCTOU window when the opp
    # flips to 'drafting' between this fetch and the delete.
    if opp["status"] in ("queued", "drafting") or not svc.delete_opportunity(
        db, opp_id
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "can't delete an opportunity while it's being drafted",
        )


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
