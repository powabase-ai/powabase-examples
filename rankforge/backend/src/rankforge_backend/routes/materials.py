"""Brand-materials endpoints (M6) — ingest the brand's own pages into a KB.

Async by design: the ingest discovers the brand's pages (crawl / sitemap / urls)
and imports each as a Powabase Source (slow), so the POST spawns a background
worker and returns 202; the GET polls `materials_progress` + the source list.
"""

import asyncio
from urllib.parse import urlparse
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
from ..models.materials import (
    DiscoveredHost,
    MaterialsDiscover,
    MaterialsDiscovery,
    MaterialsIngest,
    MaterialsSelection,
    MaterialsView,
)
from ..models.profile import CurrentUser
from ..powabase import PowabaseClient, PowabaseError
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
    # Mark 'starting' (committed) before returning, so the client's immediate poll
    # sees a running phase and engages polling — the spawned task can't lose that race.
    svc.mark_starting(db, business_id, "Gathering brand pages…")
    spawn(
        svc.ingest(
            pb, db, business_id,
            mode=payload.mode,
            url=payload.url,
            extra_urls=tuple(payload.urls),
            max_pages=payload.max_pages or svc.DEFAULT_MAX_PAGES,
            origin=payload.origin,
        )
    )
    return {"status": "started"}


@router.post(
    "/{business_id}/materials/discover",
    response_model=MaterialsDiscovery,
    dependencies=[Depends(rate_limit("materials:ingest"))],
)
async def discover_materials(
    business_id: UUID,
    payload: MaterialsDiscover,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    """Preview a crawl: discover the brand's pages (grouped by subdomain) WITHOUT
    importing anything, so the user can confirm what to ingest. Read-only."""
    assert_brand_access(db, business_id, user)
    urls = await asyncio.to_thread(
        svc.discover_pages, payload.url, payload.max_pages or svc.DEFAULT_MAX_PAGES
    )
    groups: dict[str, list[str]] = {}
    for u in urls:
        groups.setdefault(urlparse(u).netloc, []).append(u)
    hosts = [
        DiscoveredHost(host=h, urls=groups[h]) for h in sorted(groups)
    ]
    return MaterialsDiscovery(hosts=hosts, total=len(urls))


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
    # Mark 'starting' (committed) before returning so an immediate poll engages.
    svc.mark_starting(db, business_id, f"Uploading {file.filename or 'file'}…")
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


@router.post(
    "/{business_id}/materials/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("materials:ingest"))],
)
async def refresh_materials(
    business_id: UUID,
    payload: MaterialsSelection,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Re-scrape the selected URL-backed brand pages (content may have changed).
    Returns 202; poll GET /{id}/materials for progress. Uploaded files are skipped."""
    assert_brand_access(db, business_id, user)
    svc.mark_starting(db, business_id, "Refreshing selected pages…")
    spawn(svc.refresh_sources(pb, db, business_id, list(payload.row_ids)))
    return {"status": "started"}


@router.post(
    "/{business_id}/materials/bulk-delete",
    status_code=status.HTTP_202_ACCEPTED,
    # Deletes do remote Powabase teardown too — rate-limit like ingest/refresh/upload.
    dependencies=[Depends(rate_limit("materials:ingest"))],
)
async def bulk_delete_materials(
    business_id: UUID,
    payload: MaterialsSelection,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Cascade-delete the selected brand sources (mass deletion). Returns 202; poll
    GET /{id}/materials for progress."""
    assert_brand_access(db, business_id, user)
    svc.mark_starting(db, business_id, "Removing selected pages…")
    spawn(svc.remove_sources(pb, db, business_id, list(payload.row_ids)))
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
    try:
        md = await svc.source_content(pb, db, business_id, row_id)
    except PowabaseError as e:
        # The source service couldn't return content (e.g. a 502 for a broken/stale
        # source). Report it honestly instead of as a misleading 404 — and point the
        # user at the fix (refresh re-scrapes the page).
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"The source service couldn't return this page's content "
            f"(upstream HTTP {e.status_code}). It may be transient — try again, or "
            f"refresh the page to re-scrape it.",
        ) from e
    if md is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no content extracted for this source yet"
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
    dependencies=[Depends(rate_limit("materials:ingest"))],
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
