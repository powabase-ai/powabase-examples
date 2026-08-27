#!/usr/bin/env bash
set -euo pipefail
: "${PB_DB_URL:?Set PB_DB_URL to the Powabase Database URL}"

if command -v psql >/dev/null 2>&1; then
  psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -f "$1"
else
  # The URL carries the database password, so it goes in via -e rather than argv:
  # argv is visible to `docker inspect` and to any local `ps`.
  docker run --rm -i -e PGURL="$PB_DB_URL" postgres:16-alpine \
    sh -c 'exec psql "$PGURL" -v ON_ERROR_STOP=1 -f -' < "$1"
fi
