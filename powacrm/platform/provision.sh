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

# ---------------------------------------------------------------------------
# The scheduled research worker (wf-research-tick).
#
# Idempotent the same way the agent above is: find the workflow by name, create
# it only if it is missing, then PUT the whole graph. The block ids in
# wf-research-tick.json are literal UUIDs and are deliberately stable, so a
# re-provision overwrites the same blocks rather than building a new graph.
#
# Three hard-won facts encoded here:
#   1. A block id that is not a UUID makes PUT .../graph return an opaque HTTP
#      500 -- not a validation error. Never generate ids at random here; they
#      live in the JSON.
#   2. PUT .../graph answers 200 with {"blocks":0,"edges":0} for an empty
#      graph, so a 200 alone proves nothing. The counts are checked below.
#   3. The schedule lives on the starter block's config. The workflow record's
#      top-level schedule_config stays null and setting it there does nothing.
# ---------------------------------------------------------------------------

WF_NAME=$(python3 -c 'import json;print(json.load(open("wf-research-tick.json"))["name"])')
WF_DESC=$(python3 -c 'import json;print(json.load(open("wf-research-tick.json"))["description"])')

# find_workflow_id NAME -- paginates GET /api/workflows ({workflows,limit,
# offset,total}, default limit 50) so a match past the first page is not
# missed. Sets the global WF_ID; see find_agent_id above for why this
# communicates by global instead of via $(...).
find_workflow_id() {
  local name="$1" offset=0 limit=200 body id total
  WF_ID=""
  while :; do
    body=$(curl_checked GET "$BASE/api/workflows?limit=$limit&offset=$offset")
    id=$(NAME="$name" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
name = os.environ["NAME"]
print(next((w["id"] for w in d["workflows"] if w.get("name") == name), ""))
' <<<"$body")
    if [ -n "$id" ]; then
      WF_ID="$id"
      return 0
    fi
    total=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["total"])' <<<"$body")
    offset=$((offset + limit))
    if [ "$offset" -ge "$total" ]; then
      return 0
    fi
  done
}

find_workflow_id "$WF_NAME"

if [ -z "$WF_ID" ]; then
  WF_BODY=$(WF_NAME="$WF_NAME" WF_DESC="$WF_DESC" python3 -c '
import json, os
print(json.dumps({"name": os.environ["WF_NAME"], "description": os.environ["WF_DESC"]}))')
  RESP=$(curl_checked POST "$BASE/api/workflows" "$WF_BODY")
  WF_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$RESP")
  echo "created workflow $WF_NAME ($WF_ID)"
else
  echo "found workflow $WF_NAME ($WF_ID)"
fi

# AGENT_ID is resolved by name above; the graph only ever carries the
# {{AGENT_ID}} placeholder so no environment-specific id is committed. This
# guard is not ceremony: an `agent` block whose agent_id does not resolve does
# NOT fail -- it silently falls back to a default gpt-4o-mini with no system
# prompt and no tools, and answers the research prompt with small talk.
if [ -z "$AGENT_ID" ]; then
  echo "FATAL: no AGENT_ID for $AGENT_NAME; refusing to write a workflow that would silently run a default model" >&2
  exit 1
fi

GRAPH=$(AGENT_ID="$AGENT_ID" BASE="$BASE" PB_SERVICE_KEY="$PB_SERVICE_KEY" python3 -c '
import json, os, sys
g = json.load(open("wf-research-tick.json"))["graph"]
s = json.dumps(g)
for token, value in (("{{AGENT_ID}}", os.environ["AGENT_ID"]),
                     ("{{BASE}}", os.environ["BASE"]),
                     ("{{SERVICE_KEY}}", os.environ["PB_SERVICE_KEY"])):
    if token not in s:
        sys.exit("FATAL: %s is missing from %s -- refusing to deploy a half-substituted graph"
                 % (token, "wf-research-tick.json"))
    s = s.replace(token, value)
print(s)
')

SAVED=$(curl_checked PUT "$BASE/api/workflows/$WF_ID/graph" "$GRAPH")
echo "graph save: $SAVED"

# A 200 with zero blocks is a silent no-op, so compare against the file.
python3 -c '
import json, sys
want = json.load(open("wf-research-tick.json"))["graph"]
got = json.loads(sys.argv[1])
if got.get("blocks") != len(want["blocks"]) or got.get("edges") != len(want["edges"]):
    sys.exit("FATAL: graph save reported %s but the definition has %s blocks / %s edges"
             % (got, len(want["blocks"]), len(want["edges"])))
' "$SAVED"

# deploy is what actually arms the schedule (the scheduler ticks every 30s).
curl_checked POST "$BASE/api/workflows/$WF_ID/deploy" >/dev/null
echo "deployed $WF_NAME"

# Read the graph back -- there is no GET .../graph, it comes off the workflow --
# and confirm the starter still carries the schedule. This is the check that
# catches a schedule silently dropped on save.
curl_checked GET "$BASE/api/workflows/$WF_ID" | python3 -c '
import json, sys
d = json.load(sys.stdin)
blocks = d.get("blocks") or []
starter = next((b for b in blocks if b.get("type") == "starter"), None)
if starter is None:
    sys.exit("FATAL: saved graph has no starter block")
cfg = starter.get("config") or {}
missing = [k for k in ("schedule_enabled", "schedule_type",
                       "schedule_interval_value", "schedule_interval_unit") if k not in cfg]
if missing:
    sys.exit("FATAL: starter block lost schedule keys %s" % missing)
if not cfg.get("schedule_enabled"):
    sys.exit("FATAL: starter block saved with schedule_enabled false")
print("verified %d blocks, %d edges; schedule: every %s %s"
      % (len(blocks), len(d.get("edges") or []),
         cfg.get("schedule_interval_value"), cfg.get("schedule_interval_unit")))
'
echo "WF_ID=$WF_ID"
