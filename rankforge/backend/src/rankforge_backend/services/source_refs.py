"""Reference counting for project-wide Powabase Sources (safe cross-workspace deletes).

Powabase Sources are deduplicated PROJECT-WIDE (by URL / content hash), but RankForge
runs many workspaces over a single Powabase project — so one Source can be referenced
from several workspaces at once: two brands that uploaded the same page, a brand
material that is also a research source, a cluster's index doc, etc. When a workspace
removes *its* reference, the underlying Powabase Source must only be deleted if this
was the LAST reference; otherwise every other workspace still using it would break.

This module is the single place that knows every table holding a Powabase source_id:
`brand_sources`, `research_sources`, and `content_clusters.index_doc_id`.
"""

from uuid import UUID

from ..db import Database


def source_reference_count(db: Database, source_id: str | None) -> int:
    """How many rows — across ALL workspaces — reference this Powabase Source.
    ``0`` means nothing needs it, so the project-wide Source is safe to delete.

    Counts `brand_sources`, `research_sources`, and `content_clusters.index_doc_id`.

    CONTRACT: callers remove THEIR OWN reference (delete/null the row) BEFORE calling
    this, then delete the Source iff the count is 0. Counting after the row is gone is
    what makes concurrent removals orphan-safe — two removers of rows sharing one
    Source can't both still see the other and both skip. Deliberately NOT org-scoped:
    the Source is a shared project resource, so any referencing row anywhere keeps it.
    """
    if not source_id:
        return 0
    row = db.fetch_one(
        "select count(*) as n from ("
        "select 1 from public.brand_sources where source_id = %s "
        "union all "
        "select 1 from public.research_sources where source_id = %s "
        "union all "
        "select 1 from public.content_clusters where index_doc_id = %s"
        ") t",
        (source_id, source_id, source_id),
    )
    return (row or {}).get("n", 0)


def brand_exclusive_source_ids(db: Database, business_id: UUID) -> list[str]:
    """Powabase Source ids referenced ONLY by this brand — by no other workspace.

    Used when deleting a workspace: its uploaded Sources would otherwise leak in the
    Powabase project (the cascade drops the tracking rows but not the Sources). These
    are safe to delete; any Source also used by another workspace is excluded so we
    never break it. (A concurrent same-Source upload in another workspace is an
    accepted, very narrow race — workspace deletion is rare and admin-initiated.)
    """
    rows = db.fetch_all(
        """
        with owned as (
            select source_id as sid from public.brand_sources
                where business_id = %(bid)s and source_id is not null
            union
            select rs.source_id from public.research_sources rs
                join public.research_runs rr on rr.id = rs.research_run_id
                where rr.business_id = %(bid)s and rs.source_id is not null
            union
            select index_doc_id from public.content_clusters
                where business_id = %(bid)s and index_doc_id is not null
        )
        select distinct o.sid from owned o
        where not exists (
                select 1 from public.brand_sources b
                where b.source_id = o.sid and b.business_id <> %(bid)s)
          and not exists (
                select 1 from public.research_sources rs2
                join public.research_runs rr2 on rr2.id = rs2.research_run_id
                where rs2.source_id = o.sid and rr2.business_id <> %(bid)s)
          and not exists (
                select 1 from public.content_clusters c
                where c.index_doc_id = o.sid and c.business_id <> %(bid)s)
        """,
        {"bid": business_id},
    )
    return [r["sid"] for r in rows]
