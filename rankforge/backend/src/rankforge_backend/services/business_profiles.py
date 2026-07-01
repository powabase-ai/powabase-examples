"""business_profiles CRUD over the pooled Database (direct Postgres)."""

from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from ..db import Database
from ..models.business import BusinessProfileCreate, BusinessProfileUpdate

_COLUMNS = (
    "id, org_id, name, domain, description, niche, audience, seed_topics, "
    "target_keywords, competitors, brand_kb_id, sitemap_url, url_pattern, "
    "default_author, "
    "materials_kb_id, materials_progress, cluster_kb_id, created_by, "
    "created_at, updated_at"
)

# jsonb columns need the Json wrapper for psycopg adaptation.
_JSONB_FIELDS = {"seed_topics", "target_keywords", "competitors"}


def list_profiles(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"select {_COLUMNS} from public.business_profiles "
        "where org_id = %s order by created_at desc",
        (org_id,),
    )


def get_profile(db: Database, profile_id: UUID) -> dict[str, Any] | None:
    """Fetch a brand by id WITHOUT org filtering — for internal/pipeline use where
    the business_id is already trusted. Org-scoped routes either pass through
    `auth.assert_brand_access` first or compare the returned `org_id`."""
    return db.fetch_one(
        f"select {_COLUMNS} from public.business_profiles where id = %s",
        (profile_id,),
    )


def name_exists(db: Database, name: str, org_id: UUID) -> bool:
    """Case-insensitive name check, scoped to the org (names need only be unique
    within a workspace)."""
    return (
        db.fetch_one(
            "select 1 from public.business_profiles "
            "where lower(name) = lower(%s) and org_id = %s limit 1",
            (name, org_id),
        )
        is not None
    )


def create_profile(
    db: Database, data: BusinessProfileCreate, org_id: UUID
) -> dict[str, Any]:
    return db.fetch_one(
        f"""
        insert into public.business_profiles
            (org_id, name, domain, description, niche, audience,
             seed_topics, target_keywords, competitors, brand_kb_id, sitemap_url,
             url_pattern, default_author)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning {_COLUMNS}
        """,
        (
            org_id,
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
            data.url_pattern,
            data.default_author,
        ),
    )


def update_profile(
    db: Database, profile_id: UUID, data: BusinessProfileUpdate, org_id: UUID
) -> dict[str, Any] | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        row = get_profile(db, profile_id)
        return row if row and row.get("org_id") == org_id else None

    set_clauses: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        # keys come from a fixed Pydantic model → safe to interpolate as column names
        set_clauses.append(f"{key} = %s")
        params.append(Json(value) if key in _JSONB_FIELDS else value)
    set_clauses.append("updated_at = now()")
    params.extend([profile_id, org_id])

    return db.fetch_one(
        f"update public.business_profiles set {', '.join(set_clauses)} "
        f"where id = %s and org_id = %s returning {_COLUMNS}",
        tuple(params),
    )


def delete_profile(db: Database, profile_id: UUID, org_id: UUID) -> None:
    db.execute(
        "delete from public.business_profiles where id = %s and org_id = %s",
        (profile_id, org_id),
    )
