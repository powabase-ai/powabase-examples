#!/usr/bin/env bash
# Creates (or updates) the powacrm-researcher Powabase agent from
# researcher-agent.json and reconciles its attached tools to exactly what
# that file declares -- adding anything missing and removing anything the
# live agent has that isn't listed (see README.md's security note: this
# agent must never carry a database or code-execution tool). Idempotent --
# re-running finds the existing agent by name instead of creating a
# duplicate.
#
# Usage:
#   export VITE_POWABASE_URL=https://<ref>.p.powabase.ai
#   export PB_SERVICE_KEY=<service role key>     # server-side only, never in app/
#   ./platform/provision.sh
set -euo pipefail
: "${VITE_POWABASE_URL:?Set VITE_POWABASE_URL}" "${PB_SERVICE_KEY:?Set PB_SERVICE_KEY}"
BASE="$VITE_POWABASE_URL"
H=(-H "apikey: $PB_SERVICE_KEY" -H "Authorization: Bearer $PB_SERVICE_KEY" -H "Content-Type: application/json")
cd "$(dirname "$0")"

TMP_BODY="$(mktemp)"
trap 'rm -f "$TMP_BODY"' EXIT

# curl_checked METHOD URL [DATA]
# Performs the call and fails loudly (naming the call and the HTTP status)
# on a network error or a non-2xx response. On success, prints the response
# body to stdout. Every network call in this script goes through this.
curl_checked() {
  local method="$1" url="$2" data="${3:-}" status curl_exit
  set +e
  if [ -n "$data" ]; then
    status=$(curl -s -o "$TMP_BODY" -w '%{http_code}' -X "$method" "$url" "${H[@]}" -d "$data")
  else
    status=$(curl -s -o "$TMP_BODY" -w '%{http_code}' -X "$method" "$url" "${H[@]}")
  fi
  curl_exit=$?
  set -e
  if [ "$curl_exit" -ne 0 ]; then
    echo "FATAL: $method $url -- curl failed (exit $curl_exit); no HTTP response received" >&2
    exit 1
  fi
  if [ "${status:0:1}" != "2" ]; then
    echo "FATAL: $method $url failed with HTTP $status" >&2
    echo "response body: $(cat "$TMP_BODY")" >&2
    exit 1
  fi
  cat "$TMP_BODY"
}

AGENT_NAME=$(python3 -c 'import json;print(json.load(open("researcher-agent.json"))["name"])')
DESIRED_TOOLS=$(python3 -c 'import json;print(" ".join(json.load(open("researcher-agent.json"))["tools"]))')

# find_agent_id NAME -- paginates GET /api/agents (?limit=&offset=, total in
# the response) so a name match beyond the first page is never missed. Sets
# the global AGENT_ID (empty if no match). Deliberately NOT called via a
# $(...) capture: bash's `set -e` does not propagate a failure out of a
# command substitution that is itself nested inside another command
# substitution (verified empirically -- a curl_checked failure two levels
# deep was silently swallowed and looped instead of exiting), so this
# function communicates via a global variable and is invoked as a plain
# statement instead.
find_agent_id() {
  local name="$1" offset=0 limit=200 body id total
  AGENT_ID=""
  while :; do
    body=$(curl_checked GET "$BASE/api/agents?limit=$limit&offset=$offset")
    id=$(NAME="$name" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
name = os.environ["NAME"]
print(next((a["id"] for a in d["agents"] if a.get("name") == name), ""))
' <<<"$body")
    if [ -n "$id" ]; then
      AGENT_ID="$id"
      return 0
    fi
    total=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["total"])' <<<"$body")
    offset=$((offset + limit))
    if [ "$offset" -ge "$total" ]; then
      return 0
    fi
  done
}

find_agent_id "$AGENT_NAME"

if [ -z "$AGENT_ID" ]; then
  # NOTE: a "tools" array in this body is SILENTLY DROPPED. Tools attach below.
  BODY=$(python3 -c 'import json
d=json.load(open("researcher-agent.json")); d.pop("tools",None); print(json.dumps(d))')
  RESP=$(curl_checked POST "$BASE/api/agents" "$BODY")
  AGENT_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$RESP")
  echo "created agent $AGENT_NAME ($AGENT_ID)"
else
  BODY=$(python3 -c 'import json
d=json.load(open("researcher-agent.json")); d.pop("tools",None); d.pop("name",None); print(json.dumps(d))')
  curl_checked PATCH "$BASE/api/agents/$AGENT_ID" "$BODY" >/dev/null
  echo "updated agent $AGENT_NAME ($AGENT_ID)"
fi

# Reconcile tools -- this endpoint is the ONLY way to attach them (passing
# them on create/update returns 2xx and silently keeps none). Reconciliation
# is two-way: attach anything missing, and remove anything attached that
# isn't in researcher-agent.json. The removal half matters because this
# agent reads attacker-controlled web pages while its DB tools (if any got
# attached, e.g. by hand in the dashboard) would run as superuser with RLS
# bypassed -- a stray tool must not silently survive a re-provision.
TOOLS_JSON=$(curl_checked GET "$BASE/api/agents/$AGENT_ID/tools")

for t in $DESIRED_TOOLS; do
  HAVE_T=$(TOOL="$t" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
tool = os.environ["TOOL"]
print("yes" if any(x.get("tool_name") == tool for x in d["tools"]) else "no")
' <<<"$TOOLS_JSON")
  if [ "$HAVE_T" = "yes" ]; then
    echo "  tool $t already attached"
  else
    curl_checked POST "$BASE/api/agents/$AGENT_ID/tools" "{\"tool_type\":\"builtin\",\"tool_name\":\"$t\"}" >/dev/null
    echo "  attached tool $t"
  fi
done

EXTRA=$(DESIRED="$DESIRED_TOOLS" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
desired = set(os.environ["DESIRED"].split())
for x in d["tools"]:
    if x.get("tool_name") not in desired:
        print(x["id"], x.get("tool_name"))
' <<<"$TOOLS_JSON")

if [ -n "$EXTRA" ]; then
  while read -r assignment_id tool_name; do
    [ -z "$assignment_id" ] && continue
    echo "  WARNING: unexpected tool '$tool_name' is attached but not in researcher-agent.json -- removing it" >&2
    curl_checked DELETE "$BASE/api/agents/$AGENT_ID/tools/$assignment_id" >/dev/null
    echo "  removed tool $tool_name"
  done <<<"$EXTRA"
fi

FINAL_TOOLS=$(curl_checked GET "$BASE/api/agents/$AGENT_ID/tools" \
  | python3 -c 'import json,sys; print(sorted(t.get("tool_name","") for t in json.load(sys.stdin)["tools"]))')
echo "final tools on $AGENT_ID: $FINAL_TOOLS"
echo "AGENT_ID=$AGENT_ID"
