"""Article (Stage C) endpoints — async generation + status polling."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import assert_brand_access, get_current_user, require_editor
from ..db import Database
from ..models.article import (
    Article,
    ArticleGenerate,
    ArticleSummary,
    ArticleUpdate,
    ArticleVersion,
    RefineRequest,
)
from ..models.comment import Comment, CommentCreate, CommentUpdate
from ..models.linking import BrokenLink, LinkSuggestion, RemoveLinkRequest
from ..models.profile import CurrentUser
from ..powabase import PowabaseClient
from ..ratelimit import rate_limit
from ..services import comments as comments_svc
from ..services import generation as svc
from ..services import geo_optimize as geo_svc
from ..services import linkcheck as linkcheck_svc
from ..services import linking as linking_svc
from ..services import publishing as pub_svc
from ..services import quality as quality_svc
from ..services import revise as revise_svc
from ..services import scoring as scoring_svc
from ..tasks import spawn
from .deps import get_db, get_powabase

log = logging.getLogger("rankforge.routes.articles")

# Every article endpoint requires an authenticated user.
router = APIRouter(
    prefix="/api/articles",
    tags=["articles"],
    dependencies=[Depends(get_current_user)],
)

# Editor-controlled states. Entering one (approve/publish) OR leaving one
# (un-approve / un-publish / take a live article down) is an editorial decision —
# writers may only move between their own states (draft, in_review).
_EDITORIAL_STATUSES = {"approved", "published"}


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
    "/{article_id}/retry",
    response_model=Article,
    dependencies=[Depends(rate_limit("article:generate"))],
)
async def retry_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Re-run generation for a failed/interrupted draft, reusing its brief.
    Returns immediately; poll GET /api/articles/{id}."""
    article = _guard_article(db, article_id, user)
    brief = (
        svc.get_brief(db, article["brief_id"]) if article.get("brief_id") else None
    )
    if brief is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "no brief to regenerate this article from"
        )
    # Atomic claim: refuse if a pipeline is already running (double-submit guard).
    if not svc.try_begin_generation(db, article_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "generation already in progress"
        )
    spawn(svc.run_generation_task(pb, db, article_id=article_id, brief=brief))
    return svc.get_article(db, article_id)


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


async def _refine_and_finish(
    pb: PowabaseClient, db: Database, article_id: UUID,
    targets: list[str] | None = None,
) -> None:
    failed = False
    try:
        await revise_svc.refine(pb, db, article_id, targets=targets)
    except Exception:  # noqa: BLE001 — surface an infra failure, don't report a no-op
        # refine() only propagates when a pass raised before doing ANY work (e.g. the
        # reviser agent is misconfigured / unreachable). That's a real failure — mark it
        # so the user sees an error instead of "refine complete" over an unchanged draft.
        log.exception("refine pipeline failed for %s", article_id)
        failed = True
    # Return the article to a terminal status. Empty content (bailed on a broken article)
    # or a propagated infra failure → 'failed'; otherwise 'done'.
    final = svc.get_article(db, article_id)
    words = ((final or {}).get("content_md") or "").split()
    if failed or not words:
        svc._update(
            db, article_id,
            generation_status="failed",
            generation_error="refine failed — see server logs",
            progress={"phase": "failed", "word_count": len(words)},
        )
        return
    # Best-effort, BEFORE flipping to done: a refine pass rewrites the body and can
    # introduce (or fix) links, so re-validate outbound links — dead URLs surface in the
    # Links panel without a manual check. Never let it disturb the status.
    if final and final.get("business_id"):
        try:
            await linkcheck_svc.check_article(db, final["business_id"], article_id)
        except Exception:  # noqa: BLE001 — link check is advisory
            log.exception("post-refine link check failed for %s", article_id)
    svc._update(
        db, article_id,
        generation_status="done",
        progress={"phase": "done", "word_count": len(words)},
    )


@router.post(
    "/{article_id}/refine",
    response_model=Article,
    dependencies=[Depends(rate_limit("article:refine"))],
)
async def refine_article(
    article_id: UUID,
    body: RefineRequest | None = None,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Refine the draft (async). With `body.targets`, fix exactly the selected flagged
    issues; without it, auto-iterate every below-target axis."""
    _guard_article(db, article_id, user)
    # Atomically claim the article; refuse if a generation/refine is already running
    # so a double-submit can't launch two concurrent pipelines on the same article.
    if not svc.try_begin_refine(
        db, article_id, total=revise_svc.MAX_REVISIONS
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "generation already in progress"
        )
    # Normalize an empty selection (`{"targets": []}`) to None so it runs the legacy
    # auto-refine instead of taking the targeted path into a guaranteed no-op (which
    # would still burn a rate-limit token for zero work).
    targets = (body.targets or None) if body else None
    spawn(_refine_and_finish(pb, db, article_id, targets))
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
    article = _guard_article(db, article_id, user)
    # Render the SAME sanitized HTML the public /p/{id} page ships (resolve internal
    # refs → markdown → nh3) so the in-app preview shows exactly what publishes — a
    # tracking <img>/phishing <a> smuggled in via a scraped source renders live here
    # too, instead of as inert markdown text the reviewer would never notice.
    md = article.get("content_md") or ""
    resolved = linking_svc.resolve_links(db, article.get("business_id"), md) if md else ""
    return {**article, "content_html": pub_svc.render_body_html(resolved)}


@router.patch("/{article_id}", response_model=Article)
def update_article(
    article_id: UUID,
    payload: ArticleUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    article = _guard_article(db, article_id, user)
    fields = payload.model_dump(exclude_unset=True)
    # Editorial gate: a status change that ENTERS or LEAVES an editor-controlled state
    # is editor/admin only. This blocks not just approve/publish but also a writer
    # reverting a published/approved article (un-publish / un-approve / take-down),
    # which would silently reverse an editor's decision. Writers may still move freely
    # between their own states (draft, in_review).
    new_status = fields.get("status")
    current_status = article.get("status")
    if (
        new_status is not None
        and new_status != current_status
        and (
            new_status in _EDITORIAL_STATUSES
            or current_status in _EDITORIAL_STATUSES
        )
        and user.role not in ("editor", "admin")
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"only editors or admins can change status from "
            f"'{current_status}' to '{new_status}'",
        )
    row = svc.update_article(db, article_id, fields)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")
    return row


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_article(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_editor),
):
    """Permanently delete an article and everything attached to it (versions,
    comments, internal-link suggestions, broken-link findings, publication records).
    If it was a cluster's pillar, the cluster is left pillar-less, not removed."""
    _guard_article(db, article_id, user)
    if not svc.delete_article(db, article_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")


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


# --- internal links (M6 / Phase 12.1) ---
def _require_editor(user: CurrentUser) -> None:
    if user.role not in ("editor", "admin"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only editors or admins can change links"
        )


@router.get("/{article_id}/links", response_model=list[LinkSuggestion])
def list_link_suggestions(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Pending internal-link suggestions for this article."""
    _guard_article(db, article_id, user)
    return linking_svc.list_suggestions(db, article_id)


@router.post("/{article_id}/links/suggest", response_model=list[LinkSuggestion])
def suggest_link_suggestions(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Scan this article for unlinked mentions of the brand's other published
    articles and stage them as suggestions. Idempotent."""
    article = _guard_article(db, article_id, user)
    _require_editor(user)
    linking_svc.suggest_links(db, article["business_id"], article_id)
    return linking_svc.list_suggestions(db, article_id)


@router.post(
    "/{article_id}/links/{suggestion_id}/apply", response_model=LinkSuggestion
)
def apply_link_suggestion(
    article_id: UUID,
    suggestion_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Insert the link into the body, re-score (SEO), and mark the suggestion accepted."""
    article = _guard_article(db, article_id, user)
    _require_editor(user)
    row = linking_svc.apply_suggestion(db, article["business_id"], suggestion_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "link suggestion not found or not pending"
        )
    return row


@router.post(
    "/{article_id}/links/{suggestion_id}/generate", response_model=LinkSuggestion
)
async def generate_gap_link(
    article_id: UUID,
    suggestion_id: UUID,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Fill a structural gap with an LLM-written contextual link, insert it, re-score."""
    article = _guard_article(db, article_id, user)
    _require_editor(user)
    row = await linking_svc.generate_gap_link(
        pb, db, article["business_id"], suggestion_id
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no fillable gap, or the model couldn't write a usable link",
        )
    return row


@router.post(
    "/{article_id}/links/{suggestion_id}/dismiss", response_model=LinkSuggestion
)
def dismiss_link_suggestion(
    article_id: UUID,
    suggestion_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    article = _guard_article(db, article_id, user)
    _require_editor(user)
    row = linking_svc.dismiss_suggestion(db, article["business_id"], suggestion_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "link suggestion not found"
        )
    return row


# --- link health / broken links (M6 / Phase 12.3) ---
@router.get("/{article_id}/links/health", response_model=list[BrokenLink])
def list_broken_links(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Open broken-link findings for this article."""
    _guard_article(db, article_id, user)
    return linkcheck_svc.list_findings(db, article_id)


@router.post("/{article_id}/links/check", response_model=list[BrokenLink])
async def check_links(
    article_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Validate this article's outbound links now (internal targets + external URLs)
    and return the broken ones."""
    article = _guard_article(db, article_id, user)
    _require_editor(user)
    return await linkcheck_svc.check_article(db, article["business_id"], article_id)


@router.post(
    "/{article_id}/links/health/{finding_id}/ignore", response_model=BrokenLink
)
def ignore_broken_link(
    article_id: UUID,
    finding_id: UUID,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    article = _guard_article(db, article_id, user)
    _require_editor(user)
    row = linkcheck_svc.ignore_finding(db, article["business_id"], finding_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    return row


@router.post(
    "/{article_id}/links/health/{finding_id}/remove", response_model=Article
)
async def remove_broken_link(
    article_id: UUID,
    finding_id: UUID,
    body: RemoveLinkRequest | None = None,
    db: Database = Depends(get_db),
    pb: PowabaseClient = Depends(get_powabase),
    user: CurrentUser = Depends(get_current_user),
):
    """Fix a broken link in the prose: unlink (keep the words, instant) or remove it and
    let an LLM mend the sentence. Versioned + the finding closed. Returns the article."""
    article = _guard_article(db, article_id, user)
    _require_editor(user)
    updated = await linkcheck_svc.remove_link(
        pb, db, article["business_id"], article_id, finding_id,
        keep_text=body.keep_text if body else True,
    )
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    return updated


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
