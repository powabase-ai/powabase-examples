#!/usr/bin/env bash
# Regression cover for 0010_import_batch_scope.sql, over real HTTP with two real
# accounts. Like test_0009 this cannot be written in SQL: db/apply.sh connects as
# a superuser, which bypasses RLS and skips the RPC guards entirely.
#
# The case that matters is the one test_0009 structurally could not catch. Its
# RPC assertions passed A's brand AND A's batch id together, so the brand guard
# fired first and the batch predicate was never reached. The exploit needed a
# caller who legitimately owns the brand it names:
#
#     _brand_id = B's own brand   (clears the guard at the top of import_people)
#     _import_id = A's batch id   (was never checked, and the closing UPDATE was
#                                  keyed on it alone)
#
# which let B flip A's batch to completed with its counters zeroed and its errors
# erased. The whole fix is `AND brand_id = _brand_id`; a later CREATE OR REPLACE
# could silently drop it, which is exactly why this file exists.
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" \
  "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}" "${PB_DB_URL:?}"
BASE="$VITE_POWABASE_URL"; ANON="$VITE_POWABASE_ANON_KEY"

B_EMAIL="powacrm-batchscope-$(date +%s)-$$@example.com"
B_PASSWORD="scope-$(date +%s)-Xq7pW"

run_sql() { # password goes in via -e, never as argv: docker inspect and ps show argv
  if command -v psql >/dev/null 2>&1; then
    psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -t -A -c "$1"
  else
    docker run --rm -i -e PGURL="$PB_DB_URL" postgres:16-alpine \
      sh -c 'exec psql "$PGURL" -v ON_ERROR_STOP=1 -t -A -c "$0"' "$1"
  fi
}
jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
req() { curl -s -w '\n%{http_code}' "$@"; }
status() { printf '%s' "${1##*$'\n'}"; }
body() { printf '%s' "${1%$'\n'*}"; }
fail() { echo "FAIL: $*"; exit 1; }

# If the project has signups turned off -- which README's own security section
# recommends -- this test cannot run: it needs to create a second account. Skip
# rather than fail, so a correctly-hardened project still gets a green suite.
signup_disabled() { # signup_disabled <response-body>
  printf '%s' "$1" | grep -qiE 'signup(s)? (are |is )?(not allowed|disabled)|signup_disabled'
}

B_ID=""; A_IB_ID=""; B_IB_ID=""; TRIGGER_MADE=""
cleanup() {
  # Drop the fault-injection trigger first: leaving it behind would break every
  # later import on this project.
  [ -n "$TRIGGER_MADE" ] && run_sql "DROP TRIGGER IF EXISTS _t0010_boom ON people; DROP FUNCTION IF EXISTS _t0010_boom()" >/dev/null 2>&1
  [ -n "$B_ID" ] && run_sql "DELETE FROM auth.users WHERE id='$B_ID'" >/dev/null 2>&1
  [ -n "$A_IB_ID" ] && run_sql "DELETE FROM events WHERE brand_id IN (SELECT brand_id FROM import_batches WHERE id='$A_IB_ID') AND created_at > now() - interval '10 minutes'" >/dev/null 2>&1
  [ -n "$A_IB_ID" ] && run_sql "DELETE FROM import_batches WHERE id='$A_IB_ID'" >/dev/null 2>&1
  run_sql "DELETE FROM people WHERE lower(email) LIKE 'scope-probe%'" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

# --- A: the victim, with a batch in a state worth protecting -----------------
A_LOGIN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,os; print(json.dumps({"email": os.environ["PB_TEST_EMAIL"], "password": os.environ["PB_TEST_PASSWORD"]}))')")
A_TOKEN=$(printf '%s' "$A_LOGIN" | jget 'd["access_token"]')
A=(-H "apikey: $ANON" -H "Authorization: Bearer $A_TOKEN")
A_BRAND_ID=$(curl -s "$BASE/rest/v1/brands?select=id&name=eq.gpt-trainer" "${A[@]}" | jget 'd[0]["id"] if d else ""')
[ -n "$A_BRAND_ID" ] || fail "A cannot read its own seeded gpt-trainer brand"

A_IB_ID=$(curl -s -X POST "$BASE/rest/v1/import_batches" "${A[@]}" -H "Content-Type: application/json" \
  -H "Prefer: return=representation" -d "{\"brand_id\":\"$A_BRAND_ID\",\"filename\":\"_scope_victim.csv\",\"row_count\":500}" \
  | jget 'd[0]["id"]')
[ -n "$A_IB_ID" ] || fail "A cannot create its own import batch"

# --- B: self-signs-up publicly and owns a brand of its own -------------------
R=$(req -X POST "$BASE/auth/v1/signup" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "$(B_EMAIL="$B_EMAIL" B_PASSWORD="$B_PASSWORD" python3 -c 'import json,os; print(json.dumps({"email": os.environ["B_EMAIL"], "password": os.environ["B_PASSWORD"]}))')")
case "$(status "$R")" in
  2*) ;;
  *) if signup_disabled "$(body "$R")"; then
       echo "test_0010 SKIPPED (signups are disabled on this project, so a second account cannot be created)"
       exit 0
     fi
     fail "public signup is broken (HTTP $(status "$R")): $(body "$R")" ;;
esac
B_ID=$(run_sql "SELECT id FROM auth.users WHERE lower(email)=lower('$B_EMAIL')")
[ -n "$B_ID" ] || fail "signup succeeded but created no auth user"

B_TOKEN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "$(B_EMAIL="$B_EMAIL" B_PASSWORD="$B_PASSWORD" python3 -c 'import json,os; print(json.dumps({"email": os.environ["B_EMAIL"], "password": os.environ["B_PASSWORD"]}))')" \
  | jget 'd.get("access_token","")')
[ -n "$B_TOKEN" ] || fail "B could not sign in"
B=(-H "apikey: $ANON" -H "Authorization: Bearer $B_TOKEN")
B_BRAND_ID=$(curl -s "$BASE/rest/v1/brands?select=id" "${B[@]}" | jget 'd[0]["id"] if d else ""')
[ -n "$B_BRAND_ID" ] || fail "B has no starter brand"

# --- 1. THE EXPLOIT: B's own brand + A's batch id ----------------------------
BEFORE=$(run_sql "SELECT status||'|'||inserted_count||'|'||coalesce(errors::text,'[]') FROM import_batches WHERE id='$A_IB_ID'")
curl -s -X POST "$BASE/rest/v1/rpc/import_people" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_brand_id\":\"$B_BRAND_ID\",\"_import_id\":\"$A_IB_ID\",\"_rows\":[]}" >/dev/null
AFTER=$(run_sql "SELECT status||'|'||inserted_count||'|'||coalesce(errors::text,'[]') FROM import_batches WHERE id='$A_IB_ID'")
[ "$BEFORE" = "$AFTER" ] \
  || fail "B rewrote A's batch row across tenants: was [$BEFORE], now [$AFTER]"

# The guard must not turn into an existence oracle either: the call succeeds and
# simply matches no batch, exactly as an unknown id already did.
R=$(req -X POST "$BASE/rest/v1/rpc/import_people" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_brand_id\":\"$B_BRAND_ID\",\"_import_id\":\"$A_IB_ID\",\"_rows\":[]}")
case "$(status "$R")" in 2*) ;; *) fail "the batch predicate leaked an error for a foreign id (HTTP $(status "$R")): $(body "$R")" ;; esac

# --- 2. Blank-row guard: {} is not a person ----------------------------------
B_IB_ID=$(curl -s -X POST "$BASE/rest/v1/import_batches" "${B[@]}" -H "Content-Type: application/json" \
  -H "Prefer: return=representation" -d "{\"brand_id\":\"$B_BRAND_ID\",\"filename\":\"_scope_blank.csv\"}" \
  | jget 'd[0]["id"]')
OUT=$(curl -s -X POST "$BASE/rest/v1/rpc/import_people" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_brand_id\":\"$B_BRAND_ID\",\"_import_id\":\"$B_IB_ID\",\"_rows\":[{},{\"email\":\"\"}]}")
N_INS=$(printf '%s' "$OUT" | jget 'd["inserted"]')
N_ERR=$(printf '%s' "$OUT" | jget 'len(d["errors"])')
[ "$N_INS" = "0" ] || fail "a row with no identifying field was inserted (inserted=$N_INS): $OUT"
[ "$N_ERR" = "2" ] || fail "expected 2 rejected rows, got $N_ERR: $OUT"

# --- 3. SQLSTATE narrowing: a class-42 fault must abort, not be filed as data -
# Inject a failure that is NOT bad-row data. Before 0010 the per-row WHEN OTHERS
# swallowed this and the batch still finished 'completed'.
run_sql "CREATE FUNCTION _t0010_boom() RETURNS trigger LANGUAGE plpgsql AS \$\$ BEGIN RAISE EXCEPTION 'injected infrastructure fault' USING ERRCODE='42883'; END \$\$" >/dev/null
run_sql "CREATE TRIGGER _t0010_boom BEFORE INSERT ON people FOR EACH ROW EXECUTE FUNCTION _t0010_boom()" >/dev/null
TRIGGER_MADE=1
R=$(req -X POST "$BASE/rest/v1/rpc/import_people" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_brand_id\":\"$B_BRAND_ID\",\"_import_id\":\"$B_IB_ID\",\"_rows\":[{\"email\":\"scope-probe-42@example.com\"}]}")
case "$(status "$R")" in
  2*) fail "a class-42 fault was swallowed as if it were bad row data (HTTP $(status "$R")): $(body "$R")" ;;
esac
printf '%s' "$(body "$R")" | grep -q "injected infrastructure fault" \
  || fail "the import failed, but not with the injected fault -- check what actually broke: $(body "$R")"
run_sql "DROP TRIGGER _t0010_boom ON people; DROP FUNCTION _t0010_boom()" >/dev/null
TRIGGER_MADE=""

# A class-23 fault, by contrast, still belongs to its row: importing the same
# email twice is a duplicate, not an outage.
OUT=$(curl -s -X POST "$BASE/rest/v1/rpc/import_people" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_brand_id\":\"$B_BRAND_ID\",\"_import_id\":\"$B_IB_ID\",\"_rows\":[{\"email\":\"scope-probe-ok@example.com\"}]}")
[ "$(printf '%s' "$OUT" | jget 'd["inserted"]')" = "1" ] || fail "a valid row did not import once the fault was removed: $OUT"

# --- 4. anon cannot reach either RPC -----------------------------------------
# The REVOKE ... FROM PUBLIC, anon is the SINGLE control between the Anon key in
# the browser bundle and two functions that bypass every policy: both guards read
# `auth.uid() IS NOT NULL AND NOT owns_brand(...)`, which is false for anon. A
# stray GRANT would reopen full cross-tenant write with nothing else to stop it.
for fn in import_people soft_delete_person; do
  case "$fn" in
    import_people) payload="{\"_brand_id\":\"$B_BRAND_ID\",\"_import_id\":\"$B_IB_ID\",\"_rows\":[]}" ;;
    *)             payload="{\"_id\":\"$B_BRAND_ID\"}" ;;
  esac
  R=$(req -X POST "$BASE/rest/v1/rpc/$fn" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
    -H "Content-Type: application/json" -d "$payload")
  case "$(status "$R")" in
    2*) fail "anon can call $fn (HTTP $(status "$R")): $(body "$R")" ;;
  esac
  # Distinguish "revoked" from "route missing": a 404/PGRST202 would also be a
  # non-2xx, and would mean the GRANT check never actually ran.
  printf '%s' "$(body "$R")" | grep -qiE 'permission denied|not authorized|insufficient' \
    || fail "anon was refused on $fn, but not on a privilege grounds -- is the function still exposed? $(body "$R")"
done

echo "test_0010 OK"
