#!/usr/bin/env bash
# Per-owner isolation (0009_access_control.sql), proved over real HTTP with two
# real accounts.
#
# This test CANNOT be written in SQL: db/apply.sh connects as a superuser, which
# bypasses RLS, so every assertion below would pass vacuously. The only honest
# way to check "your account gets you your data and nothing else" is to hold two
# real GoTrue tokens and talk to PostgREST the way the browser does.
#
# User A is PB_TEST_EMAIL, the account that already exists. User B is created
# through GoTrue's ADMIN API with the service key (lib_second_account.sh), not by
# public signup: what this suite proves must not depend on whether the project
# accepts signups, because README tells operators to turn them off. B holds an
# ordinary user token once created -- which is the threat model -- and is deleted
# again on the way out.
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" "${PB_SERVICE_KEY:?}" \
  "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}" "${PB_DB_URL:?}"
BASE="$VITE_POWABASE_URL"; ANON="$VITE_POWABASE_ANON_KEY"
. "$(dirname "$0")/lib_second_account.sh"

# Every table a client can reach. stage_options and event_types are shared
# lookups by design (see 0009 section 4), so they are asserted separately.
OWNED=(brands companies people events import_batches views)
LOOKUPS=(stage_options event_types)

B_EMAIL="powacrm-isolation-$(date +%s)-$$@example.com"
B_PASSWORD="iso-$(date +%s)-Xq7pW"

run_sql() {
  if command -v psql >/dev/null 2>&1; then
    psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -t -A -c "$1"
  else
    docker run --rm -i -e PGURL="$PB_DB_URL" postgres:16-alpine \
      sh -c 'exec psql "$PGURL" -v ON_ERROR_STOP=1 -t -A -c "$0"' "$1"
  fi
}

jlen() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else -1)'; }
req() { curl -s -w '\n%{http_code}' "$@"; }
status() { printf '%s' "${1##*$'\n'}"; }
body() { printf '%s' "${1%$'\n'*}"; }
fail() { echo "FAIL: $*"; exit 1; }

# Assert a write was refused FOR AUTHORIZATION REASONS. A bare "not 2xx" check
# would also pass on a typo'd payload (400, missing column) and would then be
# proving nothing at all, so the refusal has to name RLS, a missing privilege,
# or one of the two RPC guards.
refused() { # refused <what> <response>
  local what="$1" r="$2" s b
  s=$(status "$r"); b=$(body "$r")
  case "$s" in 2*) fail "$what SUCCEEDED (HTTP $s): $b" ;; esac
  printf '%s' "$b" | grep -qiE 'row-level security|permission denied|not your' \
    || fail "$what was refused with HTTP $s but not on authorization grounds: $b"
}

B_ID=""; A_COMP_ID=""; A_PER_ID=""; A_IB_ID=""; A_VIEW_ID=""
cleanup() {
  # Hard deletes over the Database URL: no HTTP client can do these -- that is
  # the point of the schema -- so cleanup has to be admin-side. Deleting B
  # cascades to B's starter brand and everything under it.
  [ -n "$B_ID" ] && run_sql "DELETE FROM auth.users WHERE id='$B_ID'" >/dev/null 2>&1
  [ -n "$A_PER_ID" ] && run_sql "DELETE FROM people WHERE id='$A_PER_ID'" >/dev/null 2>&1
  [ -n "$A_COMP_ID" ] && run_sql "DELETE FROM companies WHERE id='$A_COMP_ID'" >/dev/null 2>&1
  [ -n "$A_IB_ID" ] && run_sql "DELETE FROM import_batches WHERE id='$A_IB_ID'" >/dev/null 2>&1
  [ -n "$A_VIEW_ID" ] && run_sql "DELETE FROM views WHERE id='$A_VIEW_ID'" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. User A signs in and seeds one row into every owned table, so the isolation
#    assertions below run against tables that actually hold A's data. Checking
#    "B sees nothing" against an empty table would pass with RLS switched off.
# ---------------------------------------------------------------------------
A_LOGIN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,os; print(json.dumps({"email": os.environ["PB_TEST_EMAIL"], "password": os.environ["PB_TEST_PASSWORD"]}))')")
A_TOKEN=$(printf '%s' "$A_LOGIN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
A_UID=$(printf '%s' "$A_LOGIN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["user"]["id"])')
A=(-H "apikey: $ANON" -H "Authorization: Bearer $A_TOKEN")

A_BRAND_ID=$(curl -s "$BASE/rest/v1/brands?select=id&name=eq.gpt-trainer" "${A[@]}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["id"] if d else "")')
[ -n "$A_BRAND_ID" ] || fail "A cannot read its own seeded gpt-trainer brand"

post_a() { # post_a <table> <json> -> id
  curl -s -X POST "$BASE/rest/v1/$1" "${A[@]}" -H "Content-Type: application/json" \
    -H "Prefer: return=representation" -d "$2" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["id"] if isinstance(d,list) and d else "")'
}
A_COMP_ID=$(post_a companies "{\"brand_id\":\"$A_BRAND_ID\",\"name\":\"_iso_test_co\",\"domain\":\"iso-test.example\"}")
[ -n "$A_COMP_ID" ] || fail "A cannot insert a company into its own brand"
A_PER_ID=$(post_a people "{\"brand_id\":\"$A_BRAND_ID\",\"company_id\":\"$A_COMP_ID\",\"first_name\":\"IsoTest\",\"email\":\"iso-test@example.com\"}")
[ -n "$A_PER_ID" ] || fail "A cannot insert a person into its own brand"
A_IB_ID=$(post_a import_batches "{\"brand_id\":\"$A_BRAND_ID\",\"filename\":\"_iso_test.csv\"}")
A_VIEW_ID=$(post_a views "{\"brand_id\":\"$A_BRAND_ID\",\"name\":\"_iso_test_view\",\"object\":\"people\"}")
curl -s -X POST "$BASE/rest/v1/events" "${A[@]}" -H "Content-Type: application/json" \
  -d "{\"brand_id\":\"$A_BRAND_ID\",\"person_id\":\"$A_PER_ID\",\"event_type\":\"note\",\"properties\":{\"note\":\"iso fixture\"}}" >/dev/null

# A can still update its own rows.
R=$(req -X PATCH "$BASE/rest/v1/companies?id=eq.$A_COMP_ID" "${A[@]}" -H "Content-Type: application/json" -d '{"name":"_iso_test_co_renamed"}')
case "$(status "$R")" in 2*) ;; *) fail "A cannot update its own company (HTTP $(status "$R")): $(body "$R")" ;; esac

# ---------------------------------------------------------------------------
# 2. anon -- the key that ships in the browser bundle -- sees nothing anywhere,
#    even though every owned table now holds one of A's rows.
# ---------------------------------------------------------------------------
for t in "${OWNED[@]}" "${LOOKUPS[@]}"; do
  N=$(curl -s "$BASE/rest/v1/$t?select=*&limit=1" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" | jlen)
  [ "$N" = "0" ] || fail "anon can read $t ($N)"
done

# ---------------------------------------------------------------------------
# 3. User B is created through the admin API and signs in. It must land with a
#    starter brand of its own, or B lands in an app that looks broken -- and
#    "B sees nothing of A's" would be trivially true for an account with no
#    workspace at all.
# ---------------------------------------------------------------------------
second_account "$B_EMAIL" "$B_PASSWORD" || exit $?
B_TOKEN="$SECOND_ACCOUNT_TOKEN"

B_ID=$(run_sql "SELECT id FROM auth.users WHERE lower(email)=lower('$B_EMAIL')")
[ -n "$B_ID" ] || fail "account creation reported success but created no auth user"
B=(-H "apikey: $ANON" -H "Authorization: Bearer $B_TOKEN")

# ---------------------------------------------------------------------------
# 4. B sees exactly one brand -- its own starter -- and none of A's.
# ---------------------------------------------------------------------------
B_BRANDS=$(curl -s "$BASE/rest/v1/brands?select=id,name,owner_id" "${B[@]}")
N=$(printf '%s' "$B_BRANDS" | jlen)
[ "$N" = "1" ] || fail "B sees $N brands, expected exactly its own starter: $B_BRANDS"
if printf '%s' "$B_BRANDS" | grep -q "$A_BRAND_ID"; then fail "B can see A's brand"; fi
if printf '%s' "$B_BRANDS" | grep -qi "gpt-trainer"; then fail "B can see A's brand by name"; fi

B_BRAND_ID=$(printf '%s' "$B_BRANDS" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
B_OWNER=$(printf '%s' "$B_BRANDS" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["owner_id"])')
[ "$B_OWNER" = "$B_ID" ] || fail "B's starter brand is owned by $B_OWNER, not by B"

# The starter brand exists in the database and belongs to B -- checked
# admin-side too, so this is not just what the policy chose to show.
N=$(run_sql "SELECT count(*) FROM brands WHERE owner_id='$B_ID'")
[ "$N" = "1" ] || fail "expected exactly 1 starter brand for B in the database, found $N"

# ---------------------------------------------------------------------------
# 5. B sees zero of A's rows on every table that holds user data.
# ---------------------------------------------------------------------------
for t in companies people events import_batches views; do
  N=$(curl -s "$BASE/rest/v1/$t?select=*&limit=5" "${B[@]}" | jlen)
  [ "$N" = "0" ] || fail "B can read $N row(s) of $t -- A's data is not isolated"
done

# Targeted, not just unfiltered: asking for A's rows by id must also be empty,
# in case a policy filtered on something the bare list query happened to hide.
for pair in "companies:$A_COMP_ID" "people:$A_PER_ID" "import_batches:$A_IB_ID" "views:$A_VIEW_ID" "brands:$A_BRAND_ID"; do
  t="${pair%%:*}"; id="${pair##*:}"
  [ -n "$id" ] || continue
  N=$(curl -s "$BASE/rest/v1/$t?select=id&id=eq.$id" "${B[@]}" | jlen)
  [ "$N" = "0" ] || fail "B can read A's $t row by id"
done

# The shared lookup tables ARE readable -- deliberately (0009 section 4). If
# this ever starts failing, the board and the timeline stop rendering for every
# new signup, so assert it rather than leaving it to chance.
for t in "${LOOKUPS[@]}"; do
  N=$(curl -s "$BASE/rest/v1/$t?select=*&limit=1" "${B[@]}" | jlen)
  [ "$N" = "1" ] || fail "B cannot read the shared lookup table $t ($N) -- the app will not render"
done

# ---------------------------------------------------------------------------
# 6. B cannot write into A's brand -- by any route.
# ---------------------------------------------------------------------------
refused "B inserting a company into A's brand" \
  "$(req -X POST "$BASE/rest/v1/companies" "${B[@]}" -H "Content-Type: application/json" -d "{\"brand_id\":\"$A_BRAND_ID\",\"name\":\"_b_intrusion\"}")"
refused "B inserting a person into A's brand" \
  "$(req -X POST "$BASE/rest/v1/people" "${B[@]}" -H "Content-Type: application/json" -d "{\"brand_id\":\"$A_BRAND_ID\",\"first_name\":\"Intruder\"}")"
refused "B inserting an event into A's brand" \
  "$(req -X POST "$BASE/rest/v1/events" "${B[@]}" -H "Content-Type: application/json" -d "{\"brand_id\":\"$A_BRAND_ID\",\"event_type\":\"note\",\"properties\":{}}")"
refused "B inserting an import batch into A's brand" \
  "$(req -X POST "$BASE/rest/v1/import_batches" "${B[@]}" -H "Content-Type: application/json" -d "{\"brand_id\":\"$A_BRAND_ID\",\"filename\":\"_b.csv\"}")"
refused "B inserting a view into A's brand" \
  "$(req -X POST "$BASE/rest/v1/views" "${B[@]}" -H "Content-Type: application/json" -d "{\"brand_id\":\"$A_BRAND_ID\",\"name\":\"_b_view\",\"object\":\"people\"}")"
refused "B planting a brand under A's account" \
  "$(req -X POST "$BASE/rest/v1/brands" "${B[@]}" -H "Content-Type: application/json" -d "{\"name\":\"_b_planted\",\"owner_id\":\"$(run_sql "SELECT id FROM auth.users WHERE lower(email)=lower('$PB_TEST_EMAIL')")\"}")"

# A PATCH that would move one of A's rows is a no-op rather than an error --
# UPDATE's USING clause simply matches nothing -- so assert the row is unchanged
# rather than expecting a refusal.
curl -s -X PATCH "$BASE/rest/v1/companies?id=eq.$A_COMP_ID" "${B[@]}" -H "Content-Type: application/json" -d '{"name":"_b_owns_this_now"}' >/dev/null
NAME=$(run_sql "SELECT name FROM companies WHERE id='$A_COMP_ID'")
[ "$NAME" = "_iso_test_co_renamed" ] || fail "B modified A's company (name is now '$NAME')"

# B also cannot take a brand it can see and reassign it to A, or steal A's.
curl -s -X PATCH "$BASE/rest/v1/brands?id=eq.$A_BRAND_ID" "${B[@]}" -H "Content-Type: application/json" -d "{\"owner_id\":\"$B_ID\"}" >/dev/null
OWNER=$(run_sql "SELECT owner_id FROM brands WHERE id='$A_BRAND_ID'")
[ "$OWNER" != "$B_ID" ] || fail "B took ownership of A's brand"

# ---------------------------------------------------------------------------
# 7. The SECURITY DEFINER RPCs bypass RLS by construction, so they carry their
#    own ownership check. Without it, B could write into A's brand through the
#    import RPC and destroy A's rows through the delete RPC, past every policy.
# ---------------------------------------------------------------------------
refused "B calling import_people against A's brand" \
  "$(req -X POST "$BASE/rest/v1/rpc/import_people" "${B[@]}" -H "Content-Type: application/json" \
     -d "{\"_brand_id\":\"$A_BRAND_ID\",\"_import_id\":\"$A_IB_ID\",\"_rows\":[{\"email\":\"intruder@example.com\"}]}")"
N=$(run_sql "SELECT count(*) FROM people WHERE brand_id='$A_BRAND_ID' AND lower(email)='intruder@example.com'")
[ "$N" = "0" ] || fail "import_people wrote into A's brand for B"

refused "B calling soft_delete_person on one of A's people" \
  "$(req -X POST "$BASE/rest/v1/rpc/soft_delete_person" "${B[@]}" -H "Content-Type: application/json" -d "{\"_id\":\"$A_PER_ID\"}")"
N=$(run_sql "SELECT count(*) FROM people WHERE id='$A_PER_ID' AND deleted_at IS NULL")
[ "$N" = "1" ] || fail "soft_delete_person let B tombstone A's person"

# The same two calls against B's OWN brand must work -- otherwise the guards are
# just breaking the feature rather than scoping it.
B_IB_ID=$(curl -s -X POST "$BASE/rest/v1/import_batches" "${B[@]}" -H "Content-Type: application/json" \
  -H "Prefer: return=representation" -d "{\"brand_id\":\"$B_BRAND_ID\",\"filename\":\"_b_own.csv\"}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["id"] if isinstance(d,list) and d else "")')
[ -n "$B_IB_ID" ] || fail "B cannot create an import batch in its OWN brand"
R=$(req -X POST "$BASE/rest/v1/rpc/import_people" "${B[@]}" -H "Content-Type: application/json" \
  -d "{\"_brand_id\":\"$B_BRAND_ID\",\"_import_id\":\"$B_IB_ID\",\"_rows\":[{\"first_name\":\"Bee\",\"email\":\"bee@example.com\"}]}")
case "$(status "$R")" in 2*) ;; *) fail "B cannot import into its OWN brand (HTTP $(status "$R")): $(body "$R")" ;; esac
B_PER_ID=$(run_sql "SELECT id FROM people WHERE brand_id='$B_BRAND_ID' AND lower(email)='bee@example.com'")
[ -n "$B_PER_ID" ] || fail "B's own import inserted nothing"
R=$(req -X POST "$BASE/rest/v1/rpc/soft_delete_person" "${B[@]}" -H "Content-Type: application/json" -d "{\"_id\":\"$B_PER_ID\"}")
[ "$(body "$R")" = "true" ] || fail "B cannot soft-delete its OWN person: $(body "$R")"

# ---------------------------------------------------------------------------
# 8. B cannot enumerate other users. There must be no path from an
#    `authenticated` key to the account list.
# ---------------------------------------------------------------------------
# auth.users is not in PostgREST's exposed schema (`public` only), so there is
# no /rest/v1/users to reach. Assert it rather than assume it.
R=$(req "$BASE/rest/v1/users?select=*" "${B[@]}")
case "$(status "$R")" in 2*) fail "auth.users is exposed through PostgREST: $(body "$R")" ;; esac

# GoTrue's admin list endpoint needs the Service Role key; an Anon-key session
# must not reach it.
R=$(req "$BASE/auth/v1/admin/users" "${B[@]}")
case "$(status "$R")" in 2*) fail "B can list users through the GoTrue admin API: $(body "$R")" ;; esac

# No policy leaks owner ids: the only owner_id B can select is B's own, because
# the only brand row B can select is B's own.
OWNERS=$(curl -s "$BASE/rest/v1/brands?select=owner_id" "${B[@]}" | python3 -c 'import json,sys; print(",".join(sorted({r["owner_id"] for r in json.load(sys.stdin)})))')
[ "$OWNERS" = "$B_ID" ] || fail "B can see owner ids other than its own: $OWNERS"

# Nothing else in the schema carries a user id at all -- a column named like one
# on a table B can read would be the obvious leak. Checked against the catalog
# so a future column cannot quietly reopen this.
LEAKY=$(run_sql "SELECT coalesce(string_agg(table_name || '.' || column_name, ', '), '')
                 FROM information_schema.columns
                 WHERE table_schema='public'
                   AND (column_name LIKE '%user_id%' OR column_name LIKE '%owner%' OR column_name LIKE '%auth%')
                   AND NOT (table_name='brands' AND column_name='owner_id')")
[ -z "$LEAKY" ] || fail "unexpected user-identifying column(s) in public: $LEAKY"

# ---------------------------------------------------------------------------
# 9. A is unaffected throughout: still sees its own brand and its own rows.
# ---------------------------------------------------------------------------
N=$(curl -s "$BASE/rest/v1/brands?select=id&id=eq.$A_BRAND_ID" "${A[@]}" | jlen)
[ "$N" = "1" ] || fail "A can no longer read its own brand"
N=$(curl -s "$BASE/rest/v1/companies?select=id&id=eq.$A_COMP_ID" "${A[@]}" | jlen)
[ "$N" = "1" ] || fail "A can no longer read its own company"
# Assert the property, not a count. A fresh install gives A both a starter brand
# (the signup trigger) and gpt-trainer (the seed), so pinning this to 1 passed
# only on a project whose first account predates 0009 -- i.e. the author's.
# What actually matters is that nothing A can see belongs to anyone else.
FOREIGN=$(curl -s "$BASE/rest/v1/brands?select=id,owner_id" "${A[@]}" \
  | python3 -c "import json,sys; print(','.join(b['id'] for b in json.load(sys.stdin) if b['owner_id'] != '$A_UID'))")
[ -z "$FOREIGN" ] || fail "A can see brand(s) owned by another account: $FOREIGN"
N=$(curl -s "$BASE/rest/v1/brands?select=id&id=eq.$B_BRAND_ID" "${A[@]}" | jlen)
[ "$N" = "0" ] || fail "A can see B's starter brand"

# A's sanctioned soft delete still works end to end.
R=$(req -X POST "$BASE/rest/v1/rpc/soft_delete_person" "${A[@]}" -H "Content-Type: application/json" -d "{\"_id\":\"$A_PER_ID\"}")
[ "$(body "$R")" = "true" ] || fail "A cannot soft-delete its own person: $(body "$R")"

# ---------------------------------------------------------------------------
# 10. Policy invariants that three migration headers assert but nothing tested.
#     These are about what the OWNER may do to their own rows -- the isolation
#     above is only half the model.
# ---------------------------------------------------------------------------
# 10a. A client cannot tombstone a row directly. The SELECT policy's
# `deleted_at IS NULL` is re-checked against the NEW row on UPDATE, which is why
# soft delete has to go through the RPC. Worth pinning: PostgreSQL only re-checks
# the SELECT policy when read access is required, and supabase-js `.update()`
# with no `.select()` sends `Prefer: return=minimal` -- so this could plausibly
# have differed between the raw REST call and the client the app actually uses.
# It does not; both are refused.
A_LIVE_ID=$(post_a people "{\"brand_id\":\"$A_BRAND_ID\",\"first_name\":\"IsoTombstone\",\"email\":\"iso-tombstone@example.com\"}")
[ -n "$A_LIVE_ID" ] || fail "could not create the tombstone fixture"
for pref in "return=minimal" "return=representation"; do
  R=$(req -X PATCH "$BASE/rest/v1/people?id=eq.$A_LIVE_ID" "${A[@]}" \
    -H "Content-Type: application/json" -H "Prefer: $pref" -d '{"deleted_at":"2026-01-01T00:00:00Z"}')
  case "$(status "$R")" in 2*) fail "A tombstoned its own row directly with $pref -- soft delete is meant to go through the RPC" ;; esac
done
STILL=$(run_sql "SELECT coalesce(deleted_at::text,'live') FROM people WHERE id='$A_LIVE_ID'")
[ "$STILL" = "live" ] || fail "the row was tombstoned despite the refusals (deleted_at=$STILL)"

# 10b. A cannot move its own row into another brand. The WITH CHECK on the _upd
# policies is the only thing preventing it, and nothing exercised it.
R=$(req -X PATCH "$BASE/rest/v1/people?id=eq.$A_LIVE_ID" "${A[@]}" \
  -H "Content-Type: application/json" -d "{\"brand_id\":\"$B_BRAND_ID\"}")
refused "moving A's person into B's brand" "$R"
run_sql "DELETE FROM people WHERE id='$A_LIVE_ID'" >/dev/null

# 10c. The shared lookups are readable by everyone but writable by no one.
# Note the assertion is on the DATA, not the status: with RLS on and no UPDATE
# policy, PostgREST returns 204 having matched zero rows. A test that expected a
# non-2xx here would fail while the schema was behaving perfectly.
BEFORE_LABEL=$(run_sql "SELECT label FROM stage_options WHERE value='sourced'")
req -X PATCH "$BASE/rest/v1/stage_options?value=eq.sourced" "${B[@]}" \
  -H "Content-Type: application/json" -d '{"label":"Hijacked"}' >/dev/null
req -X POST "$BASE/rest/v1/event_types" "${B[@]}" \
  -H "Content-Type: application/json" -d '{"name":"hijacked","verb":"x","label":"x","icon":"x"}' >/dev/null
AFTER_LABEL=$(run_sql "SELECT label FROM stage_options WHERE value='sourced'")
[ "$BEFORE_LABEL" = "$AFTER_LABEL" ] \
  || fail "a client rewrote a shared lookup: stage_options.sourced went from '$BEFORE_LABEL' to '$AFTER_LABEL'"
N=$(run_sql "SELECT count(*) FROM event_types WHERE name='hijacked'")
[ "$N" = "0" ] || fail "a client inserted into event_types, which every tenant reads"

echo "test_0009 OK"
