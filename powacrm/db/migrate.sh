#!/usr/bin/env bash
# Applies every migration in db/migrations/ in numeric order, then stops.
#
# Migrations are plain SQL with no version table: each one is written to be safe
# to re-run (CREATE OR REPLACE / IF NOT EXISTS) EXCEPT the table and policy
# migrations 0002-0004, which will fail on a database that already has them.
# So this is a build-from-scratch command for a fresh project, not an
# incremental "migrate to latest" -- to apply a single later migration to an
# existing database, use ./db/apply.sh db/migrations/<file>.sql.
#
# Usage: PB_DB_URL=<Database URL> ./db/migrate.sh
set -euo pipefail
cd "$(dirname "$0")"
: "${PB_DB_URL:?Set PB_DB_URL to the Powabase Database URL (Studio -> Connect -> Database URL)}"

shopt -s nullglob
files=(migrations/[0-9][0-9][0-9][0-9]_*.sql)
if [ ${#files[@]} -eq 0 ]; then
  echo "no migrations found in $(pwd)/migrations" >&2
  exit 1
fi

for f in "${files[@]}"; do
  echo "==> $f"
  ./apply.sh "$f"
done

echo "ALL MIGRATIONS APPLIED (${#files[@]} files)"
# Order matters: 0009 made brands.owner_id NOT NULL and the seed hands the demo
# brand to the first account, so the login has to exist first. Paths are shown
# relative to powacrm/, matching the README.
echo "Next, from powacrm/: ./db/setup/create_user.sh, then ./db/apply.sh db/seed/seed_gpt_trainer.sql"
