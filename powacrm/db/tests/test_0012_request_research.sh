#!/usr/bin/env bash
# request_research() over real HTTP with two real accounts.
#
# Account B comes from GoTrue's ADMIN API (lib_second_account.sh), not from
# public signup, so this suite does not switch itself off on a project that has
# signups disabled -- which is what README's security section tells operators to
# do. The assertions that need no second account at all (sections 5b and 5c: anon
# cannot call request_research, and a user token cannot reach the worker RPCs)
# run BEFORE the account is created, so even a build with no admin API and no
# signups still proves them.
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" "${PB_SERVICE_KEY:?}" \
  "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}" "${PB_DB_URL:?}"
BASE="$VITE_POWABASE_URL"; ANON="$VITE_POWABASE_ANON_KEY"
. "$(dirname "$0")/lib_second_account.sh"
B_EMAIL="powacrm-research-$(date +%s)-$$@example.com"
B_PASSWORD="res-$(date +%s)-Xq7pW"
FAKE_ID="00000000-0000-0000-0000-000000000000"

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
verdict() { printf '%s' "$1" | python3 -c "import json,sys; print(json.load(sys.stdin)['results'][0]['verdict'])"; }
jobid() { printf '%s' "$1" | python3 -c "import json,sys; v=json.load(sys.stdin)['results'][0]['job_id']; print(v if v is not None else '')"; }
detail() { printf '%s' "$1" | python3 -c "import json,sys; print(json.load(sys.stdin)['results'][0]['detail'])"; }

# Escaped -- '_' is a single-char LIKE wildcard, so an unescaped '_t12_%' also
# matches unrelated real rows such as 'at12x@...'. This runs against the real
# project database, so the escape is a safety requirement, not style.
B_ID=""; A_CO=""; A_CO2=""; A_PER=""; A_PER2=""; NODOM_CO=""; NODOM_PER=""
cleanup() {
  # By name, not by the handful of ids this suite happens to hold in variables:
  # a company whose job outlives it makes the companies DELETE below fail on the
  # foreign key and leaves fixtures behind in the real project database.
  run_sql "DELETE FROM research_jobs WHERE company_id IN (SELECT id FROM companies WHERE name LIKE '\_t12\_%')" >/dev/null 2>&1
  run_sql "DELETE FROM people WHERE lower(email) LIKE '\_t12\_%'" >/dev/null 2>&1
  run_sql "DELETE FROM companies WHERE name LIKE '\_t12\_%'" >/dev/null 2>&1
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

# 1. the happy path enqueues exactly one job, and returns a usable job_id
OUT=$(rpc_a "$A_PER")
V=$(verdict "$OUT")
[ "$V" = "queued" ] || fail "expected queued, got $V"
JOB1=$(jobid "$OUT")
[ -n "$JOB1" ] || fail "queued verdict returned a null/missing job_id"
N=$(run_sql "SELECT count(*) FROM research_jobs WHERE company_id='$A_CO'")
[ "$N" = "1" ] || fail "expected 1 job row, got $N"

# 2. asking again does not double-spend, and still hands back a job_id (Task 7's
# UI contract needs one on already_queued, not just on queued)
OUT=$(rpc_a "$A_PER")
V=$(verdict "$OUT")
[ "$V" = "already_queued" ] || fail "expected already_queued, got $V"
JOB2=$(jobid "$OUT")
[ -n "$JOB2" ] || fail "already_queued verdict returned a null job_id"
[ "$JOB2" = "$JOB1" ] || fail "already_queued returned a different job id than the original: $JOB1 vs $JOB2"
N=$(run_sql "SELECT count(*) FROM research_jobs WHERE company_id='$A_CO'")
[ "$N" = "1" ] || fail "a second request created another job (now $N)"

# 3. a company with no domain has nothing to scrape
NODOM_CO=$(post_a companies "{\"brand_id\":\"$A_BRAND\",\"name\":\"_t12_nodomain\"}")
NODOM_PER=$(post_a people "{\"brand_id\":\"$A_BRAND\",\"company_id\":\"$NODOM_CO\",\"first_name\":\"NoDom\",\"email\":\"_t12_nd@example.com\"}")
V=$(verdict "$(rpc_a "$NODOM_PER")")
[ "$V" = "skipped" ] || fail "expected skipped for a domainless company, got $V"

# 3b. a lead with no company at all is skipped the same way -- and specifically
# for that reason, not merely because it also has no domain: deleting this
# branch entirely would still yield 'skipped' via the no-domain check, so the
# detail is what actually pins down which branch fired.
NOCO_PER=$(post_a people "{\"brand_id\":\"$A_BRAND\",\"first_name\":\"NoCo\",\"email\":\"_t12_noco@example.com\"}")
OUT=$(rpc_a "$NOCO_PER")
V=$(verdict "$OUT")
[ "$V" = "skipped" ] || fail "expected skipped for a lead with no company, got $V"
D=$(detail "$OUT")
[ "$D" = "lead has no company" ] || fail "expected detail 'lead has no company', got '$D'"

# 3c. a company researched recently is skipped on freshness grounds
FRESH_CO=$(post_a companies "{\"brand_id\":\"$A_BRAND\",\"name\":\"_t12_fresh\",\"domain\":\"t12fresh.example\"}")
run_sql "UPDATE companies SET researched_at = now() - interval '1 day' WHERE id='$FRESH_CO'" >/dev/null
FRESH_PER=$(post_a people "{\"brand_id\":\"$A_BRAND\",\"company_id\":\"$FRESH_CO\",\"first_name\":\"Fresh\",\"email\":\"_t12_fresh@example.com\"}")
V=$(verdict "$(rpc_a "$FRESH_PER")")
[ "$V" = "skipped" ] || fail "expected skipped for a company researched within 30 days, got $V"

# 3d. a run that scored NOBODY does not stamp researched_at (that is deliberate:
# an empty `fit` should not lock the company for a month), which used to leave it
# re-requestable every minute forever -- a paid agent run each time, all finding
# the same nobody. A completed job inside the last 24 hours is the cooldown.
EMPTY_CO=$(post_a companies "{\"brand_id\":\"$A_BRAND\",\"name\":\"_t12_empty\",\"domain\":\"t12empty.example\"}")
EMPTY_PER=$(post_a people "{\"brand_id\":\"$A_BRAND\",\"company_id\":\"$EMPTY_CO\",\"first_name\":\"Empty\",\"email\":\"_t12_empty@example.com\"}")
run_sql "INSERT INTO research_jobs (brand_id, company_id, status, created_at, started_at, finished_at, attempts)
         VALUES ('$A_BRAND','$EMPTY_CO','done', now() - interval '2 hours', now() - interval '2 hours', now() - interval '2 hours', 1)" >/dev/null
OUT=$(rpc_a "$EMPTY_PER")
V=$(verdict "$OUT")
[ "$V" = "skipped" ] || fail "expected skipped for a company whose research finished 2 hours ago with no scores, got $V"
D=$(detail "$OUT")
case "$D" in *"24 hours"*) ;; *) fail "expected the 24-hour cooldown detail, got '$D'" ;; esac
# ...and the cooldown really is short: the same company is requestable again once
# that job is older than a day.
run_sql "UPDATE research_jobs SET finished_at = now() - interval '25 hours' WHERE company_id='$EMPTY_CO'" >/dev/null
V=$(verdict "$(rpc_a "$EMPTY_PER")")
[ "$V" = "queued" ] || fail "a company whose only completed job is 25 hours old should be requestable again, got $V"

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

# 5b. anon cannot call it at all
R=$(req -X POST "$BASE/rest/v1/rpc/request_research" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
  -H "Content-Type: application/json" -d "{\"_person_ids\":[\"$A_PER\"]}")
case "$(status "$R")" in 2*) fail "anon can call request_research" ;; esac
printf '%s' "$(body "$R")" | grep -qiE 'permission denied|not authorized|insufficient' \
  || fail "anon was refused but not on privilege grounds: $(body "$R")"

# 5c. the worker RPCs must be unreachable with a user token: a client that could
# call complete_research_job would be able to write any payload it liked.
for fn in claim_research_jobs complete_research_job fail_research_job requeue_stalled_research_jobs; do
  case "$fn" in
    claim_research_jobs)   payload='{"_limit":1}' ;;
    complete_research_job) payload="{\"_job_id\":\"$A_CO\",\"_payload\":{}}" ;;
    fail_research_job)     payload="{\"_job_id\":\"$A_CO\",\"_error\":\"x\"}" ;;
    *)                     payload='{}' ;;
  esac
  R=$(req -X POST "$BASE/rest/v1/rpc/$fn" "${A[@]}" -H "Content-Type: application/json" -d "$payload")
  case "$(status "$R")" in 2*) fail "an authenticated user can call $fn" ;; esac
  printf '%s' "$(body "$R")" | grep -qiE 'permission denied|not authorized|insufficient|does not exist' \
    || fail "$fn refused a user token, but not on privilege grounds: $(body "$R")"
done

# 6. a stranger cannot research someone else's lead
second_account "$B_EMAIL" "$B_PASSWORD" || exit $?
B_TOKEN="$SECOND_ACCOUNT_TOKEN"
B_ID=$(run_sql "SELECT id FROM auth.users WHERE lower(email)=lower('$B_EMAIL')")
[ -n "$B_ID" ] || fail "account creation reported success but created no auth user"
B=(-H "apikey: $ANON" -H "Authorization: Bearer $B_TOKEN")
B_BRAND=$(curl -s "$BASE/rest/v1/brands?select=id" "${B[@]}" | jget 'd[0]["id"]')

OUT=$(curl -s -X POST "$BASE/rest/v1/rpc/request_research" "${B[@]}" \
  -H "Content-Type: application/json" -d "{\"_person_ids\":[\"$A_PER2\"]}")
V=$(verdict "$OUT")
[ "$V" = "not_yours" ] || fail "B got '$V' for A's lead, expected not_yours"
N=$(run_sql "SELECT count(*) FROM research_jobs WHERE company_id='$A_CO2'")
[ "$N" = "0" ] || fail "B's call created a job on A's company"

# 6b. the reviewer's exploit, structural half: B cannot even create a person in
# B's OWN brand whose company_id points at A's company -- the composite FK
# rejects the write outright, before request_research is ever called.
R=$(req -X POST "$BASE/rest/v1/people" "${B[@]}" -H "Content-Type: application/json" \
  -H "Prefer: return=representation" \
  -d "{\"brand_id\":\"$B_BRAND\",\"company_id\":\"$A_CO2\",\"first_name\":\"XBrand\",\"email\":\"_t12_xbrand_rest@example.com\"}")
case "$(status "$R")" in 2*) fail "B inserted a person with a cross-brand company_id -- the composite FK did not block it" ;; esac

# 6c. the reviewer's exploit, query-level half (defense in depth): if a
# cross-brand people/company link ever exists anyway -- e.g. legacy data, or a
# bulk-load path that runs with session_replication_role=replica and so skips
# FK triggers -- request_research must still refuse it, not resolve ownership
# off person.brand_id alone (which is exactly the bug the reviewer found: B
# owns its own brand, so owns_brand(p.brand_id) passed, and the mismatched
# company_id was never checked).
# run_sql prints the multi-statement's tags too (psql -t suppresses SELECT
# footers, not command-completion tags like SET/INSERT 0 1), so pull the uuid
# out rather than trust the whole output is the id.
#
# PB_DB_URL is a pooler connection, and a pooler does NOT reset session GUCs
# when a backend is returned to the pool -- a bare `SET session_replication_role
# = replica` here would leak onto that pooled backend and silently disable FK
# enforcement and triggers for every later connection that lands on it, for
# every user of the project. `SET LOCAL` inside an explicit transaction is
# scoped to that transaction and is discarded at COMMIT regardless of how the
# pooler recycles the connection afterward.
XBRAND_PER=$(run_sql "BEGIN; SET LOCAL session_replication_role = replica; INSERT INTO people (brand_id, company_id, first_name, email) VALUES ('$B_BRAND','$A_CO2','XBrand','_t12_xbrand@example.com') RETURNING id; COMMIT;" \
  | grep -Eo '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
[ -n "$XBRAND_PER" ] || fail "could not fabricate the cross-brand-linked row needed to test the query-level guard"
OUT=$(curl -s -X POST "$BASE/rest/v1/rpc/request_research" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_person_ids\":[\"$XBRAND_PER\"]}")
V=$(verdict "$OUT")
[ "$V" = "not_yours" ] || fail "a person in B's brand linked to A's company got verdict '$V', expected not_yours"
N=$(run_sql "SELECT count(*) FROM research_jobs WHERE company_id='$A_CO2'")
[ "$N" = "0" ] || fail "the cross-brand-linked person's call created a job on A's company"

# 6d. the verdict must not become an oracle: a real lead that isn't B's and a
# person id that does not exist at all must come back byte-identical.
OUT_REAL=$(curl -s -X POST "$BASE/rest/v1/rpc/request_research" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_person_ids\":[\"$A_PER2\"]}")
OUT_FAKE=$(curl -s -X POST "$BASE/rest/v1/rpc/request_research" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_person_ids\":[\"$FAKE_ID\"]}")
REAL_TRIPLE=$(printf '%s' "$OUT_REAL" | python3 -c "import json,sys; r=json.load(sys.stdin)['results'][0]; print(json.dumps([r['verdict'],r['job_id'],r['detail']]))")
FAKE_TRIPLE=$(printf '%s' "$OUT_FAKE" | python3 -c "import json,sys; r=json.load(sys.stdin)['results'][0]; print(json.dumps([r['verdict'],r['job_id'],r['detail']]))")
[ "$REAL_TRIPLE" = "$FAKE_TRIPLE" ] || fail "a nonexistent person id is distinguishable from a real-but-not-yours one: $FAKE_TRIPLE vs $REAL_TRIPLE"

# 6e. a multi-id array returns one result per id, in the same order
OUT=$(curl -s -X POST "$BASE/rest/v1/rpc/request_research" "${A[@]}" -H "Content-Type: application/json" \
  -d "{\"_person_ids\":[\"$NODOM_PER\",\"$A_PER\",\"$FAKE_ID\"]}")
printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ids = [r['person_id'] for r in d['results']]
verdicts = [r['verdict'] for r in d['results']]
expect_ids = ['$NODOM_PER', '$A_PER', '$FAKE_ID']
expect_verdicts = ['skipped', 'already_queued', 'not_yours']
assert ids == expect_ids, ('ids', ids, expect_ids)
assert verdicts == expect_verdicts, ('verdicts', verdicts, expect_verdicts)
" || fail "a multi-id array did not return one result per id, in order: $OUT"

echo "test_0012 OK"
