#!/usr/bin/env bash
# Creates (or updates) the Powabase platform resources PowaCRM needs: the
# researcher agent and the scheduled research worker. Idempotent -- re-running
# finds existing resources by name instead of creating duplicates.
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

AGENT_NAME=$(python3 -c 'import json;print(json.load(open("researcher-agent.json"))["name"])')

AGENT_ID=$(curl -s "$BASE/api/agents" "${H[@]}" \
  | AGENT_NAME="$AGENT_NAME" python3 -c 'import json,os,sys
d=json.load(sys.stdin); name=os.environ["AGENT_NAME"]
print(next((a["id"] for a in d.get("agents",[]) if a.get("name")==name), ""))')

if [ -z "$AGENT_ID" ]; then
  # NOTE: a "tools" array in this body is SILENTLY DROPPED. Tools attach below.
  AGENT_ID=$(python3 -c 'import json
d=json.load(open("researcher-agent.json")); d.pop("tools",None); print(json.dumps(d))' \
    | curl -s -X POST "$BASE/api/agents" "${H[@]}" -d @- \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  echo "created agent $AGENT_NAME ($AGENT_ID)"
else
  python3 -c 'import json
d=json.load(open("researcher-agent.json")); d.pop("tools",None); d.pop("name",None); print(json.dumps(d))' \
    | curl -s -o /dev/null -X PATCH "$BASE/api/agents/$AGENT_ID" "${H[@]}" -d @-
  echo "updated agent $AGENT_NAME ($AGENT_ID)"
fi

# Attach builtin tools. This endpoint is the ONLY way -- passing them on create
# returns 201 and silently keeps none.
HAVE=$(curl -s "$BASE/api/agents/$AGENT_ID/tools" "${H[@]}" \
  | python3 -c 'import json,sys; print(",".join(t.get("tool_name","") for t in json.load(sys.stdin).get("tools",[])))')
for t in $(python3 -c 'import json;print(" ".join(json.load(open("researcher-agent.json"))["tools"]))'); do
  case ",$HAVE," in
    *",$t,"*) echo "  tool $t already attached" ;;
    *) curl -s -o /dev/null -X POST "$BASE/api/agents/$AGENT_ID/tools" "${H[@]}" \
         -d "{\"tool_type\":\"builtin\",\"tool_name\":\"$t\"}"
       echo "  attached tool $t" ;;
  esac
done

echo "AGENT_ID=$AGENT_ID"
