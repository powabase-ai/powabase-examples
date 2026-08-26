#!/usr/bin/env bash
set -euo pipefail
: "${PB_DB_URL:?Set PB_DB_URL to the Powabase Database URL}"

if command -v psql >/dev/null 2>&1; then
  psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -f "$1"
else
  docker run --rm -i postgres:16-alpine psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -f - < "$1"
fi
