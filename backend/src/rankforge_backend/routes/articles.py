"""Article (Stage C) endpoints — async generation + status polling."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import Database
from ..models.article import (
    Article,
    ArticleGenerate,
    ArticleSummary,
    ArticleUpdate,
)
from ..powabase import PowabaseClient
from ..services import generation as svc
from ..services import geo_optimize as geo_svc
from ..services import quality as quality_svc
from ..services import scoring as scoring_svc
from .deps import get_db, get_powabase

router = APIRouter(prefix="/api/articles", tags=["articles"])

_bg_tasks: set[asyncio.Task] = set()


@router.post("", response_model=Article, status_code=status.HTTP_201_CREATED)
async def generate_article(
    payload: ArticleGenerate,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
):
    """Generate a draft from a brief. Returns immediately; poll GET /api/articles/{id}."""
    brief = svc.get_brief(db, payload.brief_id)
    if brief is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brief not found")
    article = svc.create_article(db, brief)
    task = asyncio.create_task(
        svc.run_generation_task(pb, db, article_id=article["id"], brief=brief)
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return article


@router.post("/{article_id}/score", response_model=Article)
async def score_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
):
    """Re-run SEO + GEO scoring for an article."""
    result = await scoring_svc.score_and_store(pb, db, article_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    return svc.get_article(db, article_id)


@router.post("/{article_id}/optimize", response_model=Article)
async def optimize_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
):
    """Re-run fact-check + JSON-LD + scoring."""
    if await geo_svc.optimize_and_store(pb, db, article_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    await quality_svc.reflect(pb, db, article_id)
    await scoring_svc.score_and_store(pb, db, article_id)
    return svc.get_article(db, article_id)


@router.get("", response_model=list[ArticleSummary])
def list_articles(business_id: UUID, db: Database = Depends(get_db)):
    return svc.list_articles(db, business_id)


@router.get("/{article_id}", response_model=Article)
def get_article(article_id: UUID, db: Database = Depends(get_db)):
    row = svc.get_article(db, article_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    return row


@router.patch("/{article_id}", response_model=Article)
def update_article(
    article_id: UUID, payload: ArticleUpdate, db: Database = Depends(get_db)
):
    row = svc.update_article(db, article_id, payload.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    return row
