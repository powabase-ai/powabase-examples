"""Forward-only schema migration runner for the Powabase project DB.

Usage (from backend/):
    uv run python scripts/apply_schema.py                 # apply all pending files
    uv run python scripts/apply_schema.py schema/0012_x.sql  # force-apply specific files

How it works:
  * A `public.schema_migrations(version, applied_at)` table tracks what has been
    applied. It is created (IF NOT EXISTS) before anything else.
  * With no argv, ALL `schema/*.sql` files are discovered and sorted by filename
    (the NNNN prefix gives the order). Each file's `version` is its filename; files
    already recorded in `schema_migrations` are skipped, the rest are applied (each
    file owns its begin/commit; the connection is autocommit) and then recorded.
  * With an explicit argv list of files, those are FORCE-applied (and recorded) even
    if already present — handy for re-running an idempotent migration.

First run on an existing install (back-compat guard):
  Installs created before this runner have 0001..0010 (or 0011) applied but no rows
  in schema_migrations. To avoid re-running already-applied migrations, after
  creating the table: if it is empty AND `public.profiles` exists (a core 0001
  table) but `public.organizations` does NOT (0011 not yet applied), we BACKFILL
  0001..0010 as already-applied WITHOUT running them, then proceed. So a first run
  against such a DB only actually executes 0011 and 0012. (All migrations are
  idempotent, so this guard is about cleanliness, not correctness.)
"""

import pathlib
import sys

import psycopg

from rankforge_backend.config import get_settings

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent.parent / "schema"

# Files that pre-date the runner; backfilled (not run) on an existing pre-0011 DB.
_PRE_RUNNER_VERSIONS = [
    "0001_init.sql",
    "0002_business_profiles.sql",
    "0003_research_sources.sql",
    "0004_article_generation.sql",
    "0005_content_templates.sql",
    "0006_grounding_report.sql",
    "0007_authz_comments.sql",
    "0008_scouts.sql",
    "0009_generation_refining.sql",
    "0010_list_indexes.sql",
]


def _ensure_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        "create table if not exists public.schema_migrations ("
        "  version text primary key,"
        "  applied_at timestamptz not null default now()"
        ")"
    )


def _applied_versions(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("select version from public.schema_migrations").fetchall()
    return {r[0] for r in rows}


def _mark_applied(conn: psycopg.Connection, version: str) -> None:
    conn.execute(
        "insert into public.schema_migrations (version) values (%s) "
        "on conflict (version) do nothing",
        (version,),
    )


def _backfill_existing_install(conn: psycopg.Connection, applied: set[str]) -> None:
    """On a pre-runner DB (profiles exist, organizations don't), record 0001..0010
    as already-applied without running them."""
    if applied:
        return
    has_profiles = conn.execute(
        "select to_regclass('public.profiles')"
    ).fetchone()[0]
    has_orgs = conn.execute(
        "select to_regclass('public.organizations')"
    ).fetchone()[0]
    if has_profiles is not None and has_orgs is None:
        print(
            "existing install detected (profiles present, organizations absent): "
            "backfilling 0001..0010 as already-applied without running them"
        )
        for version in _PRE_RUNNER_VERSIONS:
            _mark_applied(conn, version)
            applied.add(version)


def _apply_file(conn: psycopg.Connection, path: pathlib.Path) -> None:
    sql = path.read_text()
    conn.execute(sql)


def main() -> None:
    url = get_settings().powabase_database_url
    if not url:
        raise SystemExit("POWABASE_DATABASE_URL is not set (check backend/.env)")

    argv_files = sys.argv[1:]
    force = bool(argv_files)

    with psycopg.connect(url, autocommit=True) as conn:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)

        if force:
            # Explicit list: force-apply (and record) exactly these, in given order.
            paths = [pathlib.Path(f) for f in argv_files]
        else:
            _backfill_existing_install(conn, applied)
            paths = sorted(SCHEMA_DIR.glob("*.sql"), key=lambda p: p.name)

        applied_now: list[str] = []
        skipped: list[str] = []
        for path in paths:
            version = path.name
            if not force and version in applied:
                skipped.append(version)
                continue
            print(f"applying {version} ...")
            _apply_file(conn, path)
            _mark_applied(conn, version)
            applied_now.append(version)
            print(f"  ok: {version}")

    if applied_now:
        print(f"applied ({len(applied_now)}): {', '.join(applied_now)}")
    else:
        print("applied (0): nothing pending")
    if skipped:
        print(f"skipped ({len(skipped)}): {', '.join(skipped)}")
    print("schema up to date.")


if __name__ == "__main__":
    main()
