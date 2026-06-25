"""Content-cluster endpoints (topical authority).

Read the brand's clusters (pillars + members), manually re-designate a pillar, and
trigger a backfill that clusters any not-yet-clustered published articles.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import assert_brand_access, get_current_user, require_editor
from ..db import Database
from ..models.clusters import ClusterDetail, ContentCluster, SetPillar
from ..models.profile import CurrentUser
from ..powabase import PowabaseClient
from ..services import clusters as svc
from ..tasks import spawn
from .deps import get_db, get_powabase

router = APIRouter(prefix="/api", tags=["clusters"],
                   dependencies=[Depends(get_current_user)])


@router.get(
    "/business-profiles/{business_id}/clusters",
    response_model=list[ContentCluster],
)
def list_clusters(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return svc.list_clusters_view(db, business_id)


@router.post(
    "/business-profiles/{business_id}/clusters/backfill",
    status_code=status.HTTP_202_ACCEPTED,
)
async def backfill_clusters(
    business_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Cluster any published articles that don't have a cluster yet (background)."""
    assert_brand_access(db, business_id, user)
    spawn(svc.backfill(pb, db, business_id))
    return {"status": "started"}


def _guard_cluster(db: Database, cluster_id: UUID, user: CurrentUser) -> dict:
    cluster = svc.get_cluster(db, cluster_id)
    if cluster is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cluster not found")
    assert_brand_access(db, cluster["business_id"], user)
    return cluster


@router.get("/clusters/{cluster_id}", response_model=ClusterDetail)
def get_cluster(
    cluster_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _guard_cluster(db, cluster_id, user)
    return svc.get_cluster_detail(db, cluster_id)


@router.post("/clusters/{cluster_id}/pillar", response_model=ClusterDetail)
def set_pillar(
    cluster_id: UUID,
    payload: SetPillar,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    """Manually designate the cluster's authority pillar (the only way it changes)."""
    cluster = _guard_cluster(db, cluster_id, user)
    row = svc.set_pillar(db, cluster["business_id"], cluster_id, payload.article_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cluster not found")
    return svc.get_cluster_detail(db, cluster_id)
