#!/usr/bin/env bash
# Stores the research worker's configuration in the project's `vault`.
#
# run_research_tick() (db/migrations/0013_inline_worker.sql) needs three things
# to call the agent: the project URL, the Service Role key, and the researcher
# agent's id. None of them may live in a table under `public` -- everything
# there is one PostgREST request away from a browser, and the Service Role key
# bypasses RLS everywhere -- and none of them may live in this repo, which is
# public. So they go in `vault`, which PostgREST does not expose and on which
# `authenticated` and `anon` hold no schema USAGE at all.
#
# Idempotent: re-running it overwrites the three secrets in place.
#
# Usage, from powacrm/:
#   export PB_DB_URL=<Database URL>              # Studio -> Connect
#   export VITE_POWABASE_URL=https://<ref>.p.powabase.ai
#   export PB_SERVICE_KEY=<service role key>     # server-side only, never in app/
#   ./db/setup/set_worker_config.sh
#
# Run ./platform/provision.sh first: this script resolves the agent by name and
# fails if it does not exist yet.
set -euo pipefail
: "${PB_DB_URL:?Set PB_DB_URL to the Powabase Database URL}"
: "${VITE_POWABASE_URL:?Set VITE_POWABASE_URL to https://<project-ref>.p.powabase.ai}"
: "${PB_SERVICE_KEY:?Set PB_SERVICE_KEY to the Service Role key for this project}"

AGENT_NAME="${PB_AGENT_NAME:-powacrm-researcher}"

# GET /api/agents paginates -- {agents, limit, offset, total}, and it caps the
# page size below whatever `limit` asks for -- so this walks the pages rather
# than assuming one request sees every agent.
AGENT_ID=$(BASE="$VITE_POWABASE_URL" KEY="$PB_SERVICE_KEY" NAME="$AGENT_NAME" python3 -c '
import json, os, sys, urllib.error, urllib.request

base, key, name = os.environ["BASE"], os.environ["KEY"], os.environ["NAME"]
headers = {"apikey": key, "Authorization": "Bearer " + key}
offset, total, seen = 0, None, 0
while True:
    url = "%s/api/agents?limit=100&offset=%d" % (base, offset)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            page = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit("GET /api/agents failed: HTTP %s %s" % (e.code, e.read()[:200].decode("utf-8", "replace")))
    agents = page.get("agents") or []
    for a in agents:
        if a.get("name") == name:
            print(a["id"])
            sys.exit(0)
    total = page.get("total") if total is None else total
    seen += len(agents)
    if not agents or (total is not None and seen >= total):
        break
    offset += len(agents)
sys.exit("no agent named %r on this project -- run ./platform/provision.sh first" % name)
')

# The values go to psql through the environment and are read with \getenv, so
# none of them lands in argv where `ps` and `docker inspect` can see it.
export PB_WORKER_URL="$VITE_POWABASE_URL"
export PB_WORKER_KEY="$PB_SERVICE_KEY"
export PB_WORKER_AGENT_ID="$AGENT_ID"

# set_research_worker_config returns names only, never values.
SQL=$(cat <<'EOSQL'
\getenv worker_url PB_WORKER_URL
\getenv worker_key PB_WORKER_KEY
\getenv worker_agent PB_WORKER_AGENT_ID
SELECT public.set_research_worker_config(:'worker_url', :'worker_key', :'worker_agent') AS stored;
EOSQL
)

if command -v psql >/dev/null 2>&1; then
  printf '%s\n' "$SQL" | psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -f -
else
  # Same reasoning as db/apply.sh: the URL carries the database password, so it
  # goes in via -e rather than argv.
  printf '%s\n' "$SQL" | docker run --rm -i \
    -e PGURL="$PB_DB_URL" -e PB_WORKER_URL -e PB_WORKER_KEY -e PB_WORKER_AGENT_ID \
    postgres:16-alpine sh -c 'exec psql "$PGURL" -v ON_ERROR_STOP=1 -f -'
fi

echo "worker config stored in vault for agent $AGENT_NAME ($AGENT_ID)"
