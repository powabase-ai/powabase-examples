"""Article (Stage C) endpoints — async generation + status polling."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import assert_brand_access, get_current_user
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
from ..ratelimit import rate_limit
from ..services import comments as comments_svc
from ..services import generation as svc
from ..services import geo_optimize as geo_svc
from ..services import quality as quality_svc
from ..services import revise as revise_svc
from ..services import scoring as scoring_svc
from ..tasks import spawn
from .deps import get_db, get_powabase

# Every article endpoint requires an authenticated user.
router = APIRouter(
    prefix="/api/articles",
    tags=["articles"],
    dependencies=[Depends(get_current_user)],
)

# Status transitions that move an article forward are gated to editors/admins.
_GATED_STATUSES = {"approved", "published"}


def _guard_article(db: Database, article_id: UUID, user: CurrentUser) -> dict:
    """Load an article and assert the caller's org owns its brand. 404 if missing
    or out-of-org. Returns the article row so callers can reuse it."""
    article = svc.get_article(db, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    assert_brand_access(db, article["business_id"], user)
    return article


def _guard_comment_article(
    db: Database, article_id: UUID, user: CurrentUser
) -> None:
    """Resolve a comment's article and assert org access via its brand. The
    comment endpoints surface a 'comment not found' 404, so we mirror that here
    rather than leaking which article ids exist in other orgs."""
    article = svc.get_article(db, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "comment not found")
    assert_brand_access(db, article["business_id"], user)


@router.post(
    "",
    response_model=Article,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("article:generate"))],
)
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
    assert_brand_access(db, brief["business_id"], user)
    article = svc.create_article(db, brief, author_id=user.id)
    spawn(svc.run_generation_task(pb, db, article_id=article["id"], brief=brief))
    return article


@router.post(
    "/{article_id}/score",
    response_model=Article,
    dependencies=[Depends(rate_limit("article:score"))],
)
async def score_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Re-run SEO + GEO scoring for an article."""
    _guard_article(db, article_id, user)
    result = await scoring_svc.score_and_store(pb, db, article_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    return svc.get_article(db, article_id)


@router.post(
    "/{article_id}/optimize",
    response_model=Article,
    dependencies=[Depends(rate_limit("article:optimize"))],
)
async def optimize_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Re-run fact-check + JSON-LD + scoring."""
    _guard_article(db, article_id, user)
    if await geo_svc.optimize_and_store(pb, db, article_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    await quality_svc.reflect(pb, db, article_id)
    await scoring_svc.score_and_store(pb, db, article_id)
    return svc.get_article(db, article_id)


async def _refine_and_finish(pb: PowabaseClient, db: Database, article_id: UUID) -> None:
    try:
        await revise_svc.refine(pb, db, article_id)
    finally:
        # Always return the article to a terminal status, even if a pass failed.
        final = svc.get_article(db, article_id)
        svc._update(
            db, article_id,
            generation_status="done",
            progress={
                "phase": "done",
                "word_count": len(((final or {}).get("content_md") or "").split()),
            },
        )


@router.post(
    "/{article_id}/refine",
    response_model=Article,
    dependencies=[Depends(rate_limit("article:refine"))],
)
async def refine_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Auto-iterate the draft against the SEO/GEO/Grounding evaluators (async)."""
    _guard_article(db, article_id, user)
    # Atomically claim the article; refuse if a generation/refine is already running
    # so a double-submit can't launch two concurrent pipelines on the same article.
    if not svc.try_begin_refine(
        db, article_id, total=revise_svc.MAX_REVISIONS
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "generation already in progress"
        )
    spawn(_refine_and_finish(pb, db, article_id))
    return svc.get_article(db, article_id)


@router.get("", response_model=list[ArticleSummary])
def list_articles(
    business_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    assert_brand_access(db, business_id, user)
    return svc.list_articles(db, business_id)


@router.get("/{article_id}", response_model=Article)
def get_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _guard_article(db, article_id, user)


@router.patch("/{article_id}", response_model=Article)
def update_article(
    article_id: UUID,
    payload: ArticleUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _guard_article(db, article_id, user)
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
def list_versions(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _guard_article(db, article_id, user)
    return svc.list_versions(db, article_id)


@router.post(
    "/{article_id}/versions/{version_id}/restore", response_model=Article
)
def restore_version(
    article_id: UUID,
    version_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _guard_article(db, article_id, user)
    row = svc.restore_version(db, article_id, version_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    return row


# --- review comments ---
@router.get("/{article_id}/comments", response_model=list[Comment])
def list_comments(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _guard_comment_article(db, article_id, user)
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
    _guard_comment_article(db, article_id, user)
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
    _guard_comment_article(db, article_id, user)
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
    _guard_comment_article(db, article_id, user)
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
