"""Apply RankForge SQL schema files to the Powabase project DB.

Usage (from backend/):
    uv run python scripts/apply_schema.py                 # applies 0001 then 0002
    uv run python scripts/apply_schema.py schema/0003_x.sql

Uses POWABASE_DATABASE_URL from backend/.env via the app settings. psycopg3 runs a
multi-statement script in one execute() when no parameters are passed; autocommit is
on so each file's own begin/commit controls its transaction.
"""

import pathlib
import sys

import psycopg

from rankforge_backend.config import get_settings

DEFAULT_FILES = ["schema/0001_init.sql", "schema/0002_business_profiles.sql"]


def main() -> None:
    files = sys.argv[1:] or DEFAULT_FILES
    url = get_settings().powabase_database_url
    if not url:
        raise SystemExit("POWABASE_DATABASE_URL is not set (check backend/.env)")

    with psycopg.connect(url, autocommit=True) as conn:
        for f in files:
            sql = pathlib.Path(f).read_text()
            print(f"applying {f} ...")
            conn.execute(sql)
            print(f"  ok: {f}")
    print("schema applied.")


if __name__ == "__main__":
    main()
