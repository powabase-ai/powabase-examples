"""Publishing & export endpoints (M8).

Two routers: an auth-gated one for export/publish, and a PUBLIC (unauthenticated)
router that serves published articles to the SSR public page so the JSON-LD is in
the server-rendered HTML — the whole point of GEO.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..auth import assert_brand_access, get_current_user, require_editor
from ..config import get_settings
from ..db import Database
from ..models.article import Article
from ..models.profile import CurrentUser
from ..models.publish import PublicArticle, Publication, PublishRequest
from ..services import generation as gen_svc
from ..services import linking as linking_svc
from ..services import publishing as svc
from .deps import get_db

router = APIRouter(
    prefix="/api/articles",
    tags=["publishing"],
    dependencies=[Depends(get_current_user)],
)

# No auth — published articles are public by definition.
public_router = APIRouter(prefix="/api/public", tags=["public"])

_EXT = {"markdown": "mdx", "html": "html"}


@router.get("/{article_id}/export")
def export_article(
    article_id: UUID,
    format: str = "markdown",
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    article = gen_svc.get_article(db, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    assert_brand_access(db, article["business_id"], user)
    result = svc.export(db, article_id, format)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "article not found or unknown format"
        )
    content, media_type = result
    # Filename is the URL slug → content/blog/<slug>.mdx serves at /blog/<slug>.
    slug = article.get("slug") or str(article_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{slug}.{_EXT[format]}"'
        },
    )


@router.post("/{article_id}/publish", response_model=Publication)
async def publish_article(
    article_id: UUID,
    payload: PublishRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),  # flips status → editor/admin only
):
    article = gen_svc.get_article(db, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    assert_brand_access(db, article["business_id"], user)
    pub = await svc.publish(
        db,
        article_id,
        target_type=payload.target_type,
        config=payload.config,
        public_base_url=get_settings().public_base_url,
    )
    if pub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    return pub


@router.post("/{article_id}/unpublish", response_model=Article)
def unpublish_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),  # changes published status
):
    """Take an article off the blog: revert it to draft while KEEPING its cluster
    membership (a pillar is demoted to a member and the cluster's pillar slot is
    vacated), so a later republish rejoins the same cluster. Use when removed from the
    blog."""
    article = gen_svc.get_article(db, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    assert_brand_access(db, article["business_id"], user)
    return svc.unpublish(db, article_id)


@router.get("/{article_id}/publications", response_model=list[Publication])
def list_publications(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    article = gen_svc.get_article(db, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    assert_brand_access(db, article["business_id"], user)
    return svc.list_publications(db, article_id)


@public_router.get("/articles/{article_id}", response_model=PublicArticle)
def public_article(article_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_published(db, article_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    # Resolve internal-link refs to live URLs, then render + sanitize fresh from
    # content_md (response_model drops content_md).
    resolved = linking_svc.resolve_links(
        db, row["business_id"], row.get("content_md") or ""
    )
    return {**row, "content_html": svc.render_body_html(resolved)}
