"""Centralized scraped-sources library."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from ..auth import assert_brand_access, get_current_user, require_editor
from ..db import Database
from ..models.profile import CurrentUser
from ..models.research import BrandSource, SourceBulkDelete, SourceMeta
from ..powabase import PowabaseClient, PowabaseError
from ..ratelimit import rate_limit
from ..services import research as svc
from ..services import source_view
from ..tasks import spawn
from .deps import get_db, get_powabase

log = logging.getLogger("rankforge.sources")

router = APIRouter(
    prefix="/api/sources",
    tags=["sources"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[BrandSource])
def list_brand_sources(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return svc.list_brand_sources(db, business_id)


@router.post("/bulk-delete")
async def bulk_delete_sources(
    payload: SourceBulkDelete,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Delete selected scraped sources from the brand's library. Editor/admin only.
    Rows are brand-scoped; each Powabase Source is removed only when unreferenced."""
    assert_brand_access(db, payload.business_id, user)
    deleted = await svc.bulk_delete_brand_sources(
        pb, db, payload.business_id, payload.row_ids
    )
    return {"deleted": deleted}


@router.post(
    "/retry",
    status_code=status.HTTP_202_ACCEPTED,
    # Triggers Firecrawl scrapes (credits + the background-task pool), exactly like
    # POST /api/research — rate-limit it the same way so one user can't exhaust them.
    dependencies=[Depends(rate_limit("sources:retry"))],
)
async def retry_sources(
    payload: SourceBulkDelete,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(require_editor),
):
    """Re-scrape the selected failed / non-extracted sources. Editor/admin only. Rows are
    brand-scoped and claimed atomically (already-extracted or in-flight rows are ignored),
    then re-scraped in the background — poll GET /api/sources for each row's status
    (retrying → extracted/failed). Returns how many rows were queued."""
    assert_brand_access(db, payload.business_id, user)
    rows = svc.mark_sources_retrying(db, payload.business_id, payload.row_ids)
    if rows:
        # Attribution: a retry burst spends scrape credits + the background-task pool, so
        # record who kicked it off and how big it was.
        log.info(
            "source retry: user %s queued %s row(s) for business %s",
            user.id, len(rows), payload.business_id,
        )
        coro = svc.retry_brand_sources(pb, db, payload.business_id, rows)
        try:
            spawn(coro)
        except Exception:
            # spawn is create_task; it realistically only fails with no running loop. Still,
            # the claim already committed — close the un-awaited coroutine and revert the
            # claim so the rows aren't stranded in 'retrying' (guarding the revert so it
            # can't mask the original error the caller needs to see).
            coro.close()
            try:
                svc.reset_sources_to_failed(db, [r["id"] for r in rows])
            except Exception:
                log.exception("retry: could not revert claim after spawn failure")
            raise
    return {"queued": len(rows)}


@router.get("/{source_id}/markdown")
async def get_source_markdown(
    source_id: str,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    if not svc.source_in_org(db, source_id, user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    try:
        md = await pb.get_source_markdown(source_id)
    except PowabaseError as e:
        # A source whose scrape failed (or is still extracting) has no markdown
        # derivative and Powabase returns 404 "No markdown derivative found". That's a
        # normal empty state, not a gateway fault — return empty markdown so the viewer
        # shows "no extracted content" instead of a scary 502. Any OTHER upstream status
        # (5xx, auth) is a genuine fault → 502, matching the /pages proxy.
        if e.status_code == status.HTTP_404_NOT_FOUND:
            return {"source_id": source_id, "markdown": ""}
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return {"source_id": source_id, "markdown": md}


@router.get("/{source_id}/meta", response_model=SourceMeta)
async def get_source_meta(
    source_id: str,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Whether this source has 'original page' renders (uploaded PDFs) and their
    dimensions, so the viewer can offer the Original-pages display mode."""
    if not svc.source_in_org(db, source_id, user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    try:
        source = await pb.get_source(source_id)
    except PowabaseError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return {"source_id": source_id, **source_view.build_page_meta(source)}


@router.get("/{source_id}/pages/{index}")
async def get_source_page_image(
    source_id: str,
    index: int = Path(ge=0),
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """One rendered page image (by its derivative-list index, from /meta). Proxied
    bytes with a private 1h cache so the browser/React-Query layer can reuse an
    authed page across scroll and remount."""
    if not svc.source_in_org(db, source_id, user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    try:
        content, content_type = await pb.get_source_derivative_image(source_id, index)
    except PowabaseError as e:
        if e.status_code == 404:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "page image not found") from e
        # The image exists but the upstream fetch failed — say so (a "not found" detail
        # would mislead), and surface the upstream cause like the markdown/meta proxies.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
