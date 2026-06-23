"""Review comments on articles (editorial collaboration)."""

from typing import Any
from uuid import UUID

from ..db import Database

_COLS = (
    "c.id, c.article_id, c.author_id, c.body, c.anchor, c.resolved, "
    "c.created_at, c.updated_at, p.email as author_email, "
    "p.display_name as author_name"
)
_JOIN = (
    "from public.article_comments c "
    "left join public.profiles p on p.id = c.author_id"
)


def list_comments(db: Database, article_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLS} {_JOIN} where c.article_id = %s order by c.created_at",
        (article_id,),
    )


def get_comment(db: Database, comment_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_COLS} {_JOIN} where c.id = %s", (comment_id,)
    )


def create_comment(
    db: Database,
    article_id: UUID,
    author_id: UUID,
    body: str,
    anchor: str | None,
) -> dict[str, Any]:
    row = db.fetch_one(
        "insert into public.article_comments (article_id, author_id, body, anchor) "
        "values (%s, %s, %s, %s) returning id",
        (article_id, author_id, body, anchor),
    )
    return get_comment(db, row["id"])


def update_comment(
    db: Database, comment_id: UUID, fields: dict[str, Any]
) -> dict[str, Any] | None:
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return get_comment(db, comment_id)
    sets = [f"{k} = %s" for k in fields]
    sets.append("updated_at = now()")
    params = [*fields.values(), comment_id]
    updated = db.fetch_one(
        f"update public.article_comments set {', '.join(sets)} "
        "where id = %s returning id",
        tuple(params),
    )
    return get_comment(db, comment_id) if updated else None


def delete_comment(db: Database, comment_id: UUID) -> bool:
    row = db.fetch_one(
        "delete from public.article_comments where id = %s returning id",
        (comment_id,),
    )
    return row is not None
