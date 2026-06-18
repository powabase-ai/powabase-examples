"""business_profiles CRUD over the pooled Database (direct Postgres)."""

from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..models.business import BusinessProfileCreate, BusinessProfileUpdate

_COLUMNS = (
    "id, name, domain, description, niche, audience, seed_topics, target_keywords, "
    "competitors, brand_kb_id, sitemap_url, created_by, created_at, updated_at"
)

# jsonb columns need the Json wrapper for psycopg adaptation.
_JSONB_FIELDS = {"seed_topics", "target_keywords", "competitors"}


def list_profiles(db: Database) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLUMNS} from public.business_profiles order by created_at desc"
    )


def get_profile(db: Database, profile_id: UUID) -> dict[str, Any] | None:
    return db.fetch_one(
        f"select {_COLUMNS} from public.business_profiles where id = %s",
        (profile_id,),
    )


def create_profile(db: Database, data: BusinessProfileCreate) -> dict[str, Any]:
    return db.fetch_one(
        f"""
        insert into public.business_profiles
            (name, domain, description, niche, audience,
             seed_topics, target_keywords, competitors, brand_kb_id, sitemap_url)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning {_COLUMNS}
        """,
        (
            data.name,
            data.domain,
            data.description,
            data.niche,
            data.audience,
            Json(data.seed_topics),
            Json(data.target_keywords),
            Json([c.model_dump() for c in data.competitors]),
            data.brand_kb_id,
            data.sitemap_url,
        ),
    )


def update_profile(
    db: Database, profile_id: UUID, data: BusinessProfileUpdate
) -> dict[str, Any] | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return get_profile(db, profile_id)

    set_clauses: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        # keys come from a fixed Pydantic model → safe to interpolate as column names
        set_clauses.append(f"{key} = %s")
        params.append(Json(value) if key in _JSONB_FIELDS else value)
    set_clauses.append("updated_at = now()")
    params.append(profile_id)

    return db.fetch_one(
        f"update public.business_profiles set {', '.join(set_clauses)} "
        f"where id = %s returning {_COLUMNS}",
        tuple(params),
    )


def delete_profile(db: Database, profile_id: UUID) -> None:
    db.execute("delete from public.business_profiles where id = %s", (profile_id,))
