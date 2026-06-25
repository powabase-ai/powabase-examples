"""Re-linking maintenance schedule (M6 / Phase 12.3).

Per-brand config for the monthly re-linking scout plus a manual "Run now". The scout
re-runs the internal-link suggester across the published library and stages
suggestions for review (services.relink); the in-process scheduler drives the cadence.
"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, status

from ..auth import assert_brand_access, get_current_user, require_editor
from ..db import Database
from ..models.linking import RelinkConfig, RelinkConfigUpdate
from ..models.profile import CurrentUser
from ..services import relink as svc
from ..tasks import spawn
from .deps import get_db

router = APIRouter(
    prefix="/api/business-profiles",
    tags=["relink"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/{business_id}/relink", response_model=RelinkConfig)
def get_relink_config(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return svc.get_config(db, business_id) or svc.default_config(business_id)


@router.put("/{business_id}/relink", response_model=RelinkConfig)
def update_relink_config(
    business_id: UUID,
    payload: RelinkConfigUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    assert_brand_access(db, business_id, user)
    return svc.update_config(db, business_id, payload.model_dump(exclude_unset=True))


@router.post("/{business_id}/relink/run", status_code=status.HTTP_202_ACCEPTED)
async def run_relink_now(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    """Re-scan the published library now (background). Poll GET /relink for results."""
    assert_brand_access(db, business_id, user)
    # run_relink is sync (pure DB) — off-load to a thread so the request returns fast.
    spawn(asyncio.to_thread(svc.run_relink, db, business_id))
    return {"status": "started"}
