#!/usr/bin/env bash
# request_research() over real HTTP with two real accounts.
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" \
  "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}" "${PB_DB_URL:?}"
BASE="$VITE_POWABASE_URL"; ANON="$VITE_POWABASE_ANON_KEY"
B_EMAIL="powacrm-research-$(date +%s)-$$@example.com"
B_PASSWORD="res-$(date +%s)-Xq7pW"

run_sql() {
  if command -v psql >/dev/null 2>&1; then psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -t -A -c "$1"
  else docker run --rm -i -e PGURL="$PB_DB_URL" postgres:16-alpine \
    sh -c 'exec psql "$PGURL" -v ON_ERROR_STOP=1 -t -A -c "$0"' "$1"; fi
}
jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
req() { curl -s -w '\n%{http_code}' "$@"; }
status() { printf '%s' "${1##*$'\n'}"; }
body() { printf '%s' "${1%$'\n'*}"; }
fail() { echo "FAIL: $*"; exit 1; }
signup_disabled() { printf '%s' "$1" | grep -qiE 'signup(s)? (are |is )?(not allowed|disabled)|signup_disabled'; }
verdict() { printf '%s' "$1" | python3 -c "import json,sys; print(json.load(sys.stdin)['results'][0]['verdict'])"; }

B_ID=""; A_CO=""; A_CO2=""; A_PER=""; A_PER2=""; NODOM_CO=""; NODOM_PER=""
cleanup() {
  run_sql "DELETE FROM research_jobs WHERE company_id IN ('${A_CO:-00000000-0000-0000-0000-000000000000}','${A_CO2:-00000000-0000-0000-0000-000000000000}','${NODOM_CO:-00000000-0000-0000-0000-000000000000}')" >/dev/null 2>&1
  run_sql "DELETE FROM people WHERE lower(email) LIKE '_t12_%'" >/dev/null 2>&1
  run_sql "DELETE FROM companies WHERE name LIKE '_t12_%'" >/dev/null 2>&1
  run_sql "UPDATE brands SET research_daily_cap = 25 WHERE name = 'gpt-trainer'" >/dev/null 2>&1
  [ -n "$B_ID" ] && run_sql "DELETE FROM auth.users WHERE id='$B_ID'" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

A_LOGIN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,os; print(json.dumps({"email": os.environ["PB_TEST_EMAIL"], "password": os.environ["PB_TEST_PASSWORD"]}))')")
A_TOKEN=$(printf '%s' "$A_LOGIN" | jget 'd["access_token"]')
A=(-H "apikey: $ANON" -H "Authorization: Bearer $A_TOKEN")
A_BRAND=$(curl -s "$BASE/rest/v1/brands?select=id&name=eq.gpt-trainer" "${A[@]}" | jget 'd[0]["id"]')

post_a() { curl -s -X POST "$BASE/rest/v1/$1" "${A[@]}" -H "Content-Type: application/json" \
  -H "Prefer: return=representation" -d "$2" | jget 'd[0]["id"]'; }
rpc_a() { curl -s -X POST "$BASE/rest/v1/rpc/request_research" "${A[@]}" \
  -H "Content-Type: application/json" -d "{\"_person_ids\":[\"$1\"]}"; }

A_CO=$(post_a companies "{\"brand_id\":\"$A_BRAND\",\"name\":\"_t12_co\",\"domain\":\"t12.example\"}")
A_PER=$(post_a people "{\"brand_id\":\"$A_BRAND\",\"company_id\":\"$A_CO\",\"first_name\":\"T12\",\"email\":\"_t12_a@example.com\"}")

# 1. the happy path enqueues exactly one job
V=$(verdict "$(rpc_a "$A_PER")")
[ "$V" = "queued" ] || fail "expected queued, got $V"
N=$(run_sql "SELECT count(*) FROM research_jobs WHERE company_id='$A_CO'")
[ "$N" = "1" ] || fail "expected 1 job row, got $N"

# 2. asking again does not double-spend
V=$(verdict "$(rpc_a "$A_PER")")
[ "$V" = "already_queued" ] || fail "expected already_queued, got $V"
N=$(run_sql "SELECT count(*) FROM research_jobs WHERE company_id='$A_CO'")
[ "$N" = "1" ] || fail "a second request created another job (now $N)"

# 3. a company with no domain has nothing to scrape
NODOM_CO=$(post_a companies "{\"brand_id\":\"$A_BRAND\",\"name\":\"_t12_nodomain\"}")
NODOM_PER=$(post_a people "{\"brand_id\":\"$A_BRAND\",\"company_id\":\"$NODOM_CO\",\"first_name\":\"NoDom\",\"email\":\"_t12_nd@example.com\"}")
V=$(verdict "$(rpc_a "$NODOM_PER")")
[ "$V" = "skipped" ] || fail "expected skipped for a domainless company, got $V"

# 4. the daily cap holds, and it is enforced inside the function
run_sql "UPDATE brands SET research_daily_cap = 1 WHERE id='$A_BRAND'" >/dev/null
A_CO2=$(post_a companies "{\"brand_id\":\"$A_BRAND\",\"name\":\"_t12_co2\",\"domain\":\"t12b.example\"}")
A_PER2=$(post_a people "{\"brand_id\":\"$A_BRAND\",\"company_id\":\"$A_CO2\",\"first_name\":\"T12b\",\"email\":\"_t12_b@example.com\"}")
V=$(verdict "$(rpc_a "$A_PER2")")
[ "$V" = "capped" ] || fail "expected capped once the brand hit its daily cap, got $V"
run_sql "UPDATE brands SET research_daily_cap = 25 WHERE id='$A_BRAND'" >/dev/null

# 5. authenticated cannot write the queue directly -- the cap would be bypassable
R=$(req -X POST "$BASE/rest/v1/research_jobs" "${A[@]}" -H "Content-Type: application/json" \
  -d "{\"brand_id\":\"$A_BRAND\",\"company_id\":\"$A_CO2\"}")
case "$(status "$R")" in 2*) fail "authenticated inserted into research_jobs directly" ;; esac

# 6. a stranger cannot research someone else's lead
R=$(req -X POST "$BASE/auth/v1/signup" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "$(B_EMAIL="$B_EMAIL" B_PASSWORD="$B_PASSWORD" python3 -c 'import json,os; print(json.dumps({"email": os.environ["B_EMAIL"], "password": os.environ["B_PASSWORD"]}))')")
case "$(status "$R")" in
  2*) ;;
  *) if signup_disabled "$(body "$R")"; then echo "test_0012 SKIPPED (signups disabled)"; exit 0; fi
     fail "public signup is broken (HTTP $(status "$R")): $(body "$R")" ;;
esac
B_ID=$(run_sql "SELECT id FROM auth.users WHERE lower(email)=lower('$B_EMAIL')")
B_TOKEN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "$(B_EMAIL="$B_EMAIL" B_PASSWORD="$B_PASSWORD" python3 -c 'import json,os; print(json.dumps({"email": os.environ["B_EMAIL"], "password": os.environ["B_PASSWORD"]}))')" | jget 'd["access_token"]')
OUT=$(curl -s -X POST "$BASE/rest/v1/rpc/request_research" -H "apikey: $ANON" -H "Authorization: Bearer $B_TOKEN" \
  -H "Content-Type: application/json" -d "{\"_person_ids\":[\"$A_PER2\"]}")
V=$(printf '%s' "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['results'][0]['verdict'])")
[ "$V" = "not_yours" ] || fail "B got '$V' for A's lead, expected not_yours"
N=$(run_sql "SELECT count(*) FROM research_jobs WHERE company_id='$A_CO2'")
[ "$N" = "0" ] || fail "B's call created a job on A's company"

# 7. anon cannot call it at all
R=$(req -X POST "$BASE/rest/v1/rpc/request_research" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
  -H "Content-Type: application/json" -d "{\"_person_ids\":[\"$A_PER\"]}")
case "$(status "$R")" in 2*) fail "anon can call request_research" ;; esac
printf '%s' "$(body "$R")" | grep -qiE 'permission denied|not authorized|insufficient' \
  || fail "anon was refused but not on privilege grounds: $(body "$R")"

echo "test_0012 OK"
