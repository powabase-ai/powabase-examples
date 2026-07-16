"""CRUD over public.linkedin_posts. Org-scoping is enforced by the route (_guard_article),
not here — this layer is pure data access (mirrors services/comments.py)."""

from typing import Any
from uuid import UUID

from ..db import Database

_COLS = "id, article_id, angle, body, created_by, created_at, updated_at"


def list_posts(db: Database, article_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLS} from public.linkedin_posts "
        "where article_id = %s order by created_at desc",
        (article_id,),
    )


def get_post(db: Database, post_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_COLS} from public.linkedin_posts where id = %s", (post_id,)
    )


def create_post(
    db: Database,
    *,
    article_id: UUID,
    business_id: UUID,
    angle: str,
    body: str,
    author_id: Any = None,
) -> dict[str, Any]:
    return db.fetch_one(
        "insert into public.linkedin_posts "
        "(article_id, business_id, angle, body, created_by) "
        f"values (%s, %s, %s, %s, %s) returning {_COLS}",
        (article_id, business_id, angle, body, author_id),
    )


def update_post(db: Database, post_id: UUID, body: str) -> dict[str, Any] | None:
    return db.fetch_one(
        f"update public.linkedin_posts set body = %s, updated_at = now() "
        f"where id = %s returning {_COLS}",
        (body, post_id),
    )


def delete_post(db: Database, post_id: UUID) -> bool:
    row = db.fetch_one(
        "delete from public.linkedin_posts where id = %s returning id", (post_id,)
    )
    return row is not None
