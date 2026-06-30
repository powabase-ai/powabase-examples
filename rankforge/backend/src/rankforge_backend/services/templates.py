"""Article-type templates (content_templates registry)."""

from typing import Any

from ..db import Database

_COLUMNS = (
    "id, type, label, outline_guidance, schema_org_type, default_word_count, "
    "geo_target, enabled"
)


def list_templates(db: Database) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLUMNS} from public.content_templates "
        "where enabled = true order by label"
    )


def get_template(db: Database, type_: str | None) -> dict[str, Any] | None:
    if not type_:
        return None
    # Only ENABLED templates are selectable (the list endpoint already filters this) —
    # otherwise a client could name a disabled article-type and still drive generation.
    return db.fetch_one(
        f"select {_COLUMNS} from public.content_templates "
        "where type = %s and enabled = true",
        (type_,),
    )
