"""Article (Stage C) endpoints — async generation + status polling."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..db import Database
from ..models.article import (
    Article,
    ArticleGenerate,
    ArticleSummary,
    ArticleUpdate,
    ArticleVersion,
)
from ..models.comment import Comment, CommentCreate, CommentUpdate
from ..models.profile import CurrentUser
from ..powabase import PowabaseClient
from ..services import comments as comments_svc
from ..services import generation as svc
from ..services import geo_optimize as geo_svc
from ..services import quality as quality_svc
from ..services import scoring as scoring_svc
from .deps import get_db, get_powabase

# Every article endpoint requires an authenticated user.
router = APIRouter(
    prefix="/api/articles",
    tags=["articles"],
    dependencies=[Depends(get_current_user)],
)

# Status transitions that move an article forward are gated to editors/admins.
_GATED_STATUSES = {"approved", "published"}

_bg_tasks: set[asyncio.Task] = set()


@router.post("", response_model=Article, status_code=status.HTTP_201_CREATED)
async def generate_article(
    payload: ArticleGenerate,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Generate a draft from a brief. Returns immediately; poll GET /api/articles/{id}."""
    brief = svc.get_brief(db, payload.brief_id)
    if brief is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brief not found")
    article = svc.create_article(db, brief, author_id=user.id)
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
    article_id: UUID,
    payload: ArticleUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    fields = payload.model_dump(exclude_unset=True)
    # Approving/publishing is an editorial gate — writers may draft & submit only.
    if (
        fields.get("status") in _GATED_STATUSES
        and user.role not in ("editor", "admin")
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"only editors or admins can set status to {fields['status']}",
        )
    row = svc.update_article(db, article_id, fields)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    return row


@router.get("/{article_id}/versions", response_model=list[ArticleVersion])
def list_versions(article_id: UUID, db: Database = Depends(get_db)):
    return svc.list_versions(db, article_id)


@router.post(
    "/{article_id}/versions/{version_id}/restore", response_model=Article
)
def restore_version(
    article_id: UUID, version_id: UUID, db: Database = Depends(get_db)
):
    row = svc.restore_version(db, article_id, version_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    return row


# --- review comments ---
@router.get("/{article_id}/comments", response_model=list[Comment])
def list_comments(article_id: UUID, db: Database = Depends(get_db)):
    return comments_svc.list_comments(db, article_id)


@router.post(
    "/{article_id}/comments",
    response_model=Comment,
    status_code=status.HTTP_201_CREATED,
)
def add_comment(
    article_id: UUID,
    payload: CommentCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return comments_svc.create_comment(
        db, article_id, user.id, payload.body, payload.anchor
    )


@router.patch("/{article_id}/comments/{comment_id}", response_model=Comment)
def edit_comment(
    article_id: UUID,
    comment_id: UUID,
    payload: CommentUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    existing = comments_svc.get_comment(db, comment_id)
    if existing is None or str(existing["article_id"]) != str(article_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "comment not found")
    # Editing the text is author-only; resolving a thread is open to any reviewer.
    if payload.body is not None and str(existing["author_id"]) != str(user.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only the author can edit a comment"
        )
    row = comments_svc.update_comment(
        db, comment_id, payload.model_dump(exclude_unset=True)
    )
    return row


@router.delete(
    "/{article_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_comment(
    article_id: UUID,
    comment_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    existing = comments_svc.get_comment(db, comment_id)
    if existing is None or str(existing["article_id"]) != str(article_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "comment not found")
    # Authors delete their own; editors/admins can moderate any thread.
    if str(existing["author_id"]) != str(user.id) and user.role not in (
        "editor",
        "admin",
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed")
    comments_svc.delete_comment(db, comment_id)
