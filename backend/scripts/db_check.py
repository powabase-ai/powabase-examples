"""Verify RankForge tables exist and reload the PostgREST schema cache.

Usage (from backend/): uv run python scripts/db_check.py
"""

import psycopg

from rankforge_backend.config import get_settings


def main() -> None:
    url = get_settings().powabase_database_url
    with psycopg.connect(url, autocommit=True) as conn:
        rows = conn.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' order by table_name"
        ).fetchall()
        print("public tables:", [r[0] for r in rows])

        cols = conn.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = 'research_runs' "
            "and column_name = 'business_id'"
        ).fetchall()
        print("research_runs.business_id present:", bool(cols))

        # PostgREST caches the schema; reload it so new tables are exposed.
        conn.execute("notify pgrst, 'reload schema'")
        print("sent: notify pgrst reload schema")


if __name__ == "__main__":
    main()
