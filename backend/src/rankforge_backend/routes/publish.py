"""Publishing & export endpoints (M8).

Two routers: an auth-gated one for export/publish, and a PUBLIC (unauthenticated)
router that serves published articles to the SSR public page so the JSON-LD is in
the server-rendered HTML — the whole point of GEO.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..auth import get_current_user
from ..config import get_settings
from ..db import Database
from ..models.publish import PublicArticle, Publication, PublishRequest
from ..services import publishing as svc
from .deps import get_db

router = APIRouter(
    prefix="/api/articles",
    tags=["publishing"],
    dependencies=[Depends(get_current_user)],
)

# No auth — published articles are public by definition.
public_router = APIRouter(prefix="/api/public", tags=["public"])

_EXT = {"markdown": "md", "html": "html"}


@router.get("/{article_id}/export")
def export_article(article_id: UUID, format: str = "markdown", db: Database = Depends(get_db)):
    result = svc.export(db, article_id, format)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "article not found or unknown format"
        )
    content, media_type = result
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="article.{_EXT[format]}"'
        },
    )


@router.post("/{article_id}/publish", response_model=Publication)
async def publish_article(
    article_id: UUID, payload: PublishRequest, db: Database = Depends(get_db)
):
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


@router.get("/{article_id}/publications", response_model=list[Publication])
def list_publications(article_id: UUID, db: Database = Depends(get_db)):
    return svc.list_publications(db, article_id)


@public_router.get("/articles/{article_id}", response_model=PublicArticle)
def public_article(article_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_published(db, article_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return row
