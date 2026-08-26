#!/usr/bin/env bash
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}" "${PB_DB_URL:?}"
BASE="$VITE_POWABASE_URL"; ANON="$VITE_POWABASE_ANON_KEY"

# Admin-level SQL escape hatch for fixture cleanup only (RLS gives
# `authenticated` no DELETE policy on any table by design -- soft delete is
# the only sanctioned removal path, and it doesn't apply to every table this
# test touches).
run_sql() {
  if command -v psql >/dev/null 2>&1; then
    psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -t -A -c "$1"
  else
    docker run --rm -i postgres:16-alpine psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -t -A -c "$1"
  fi
}

FIXTURE_COMP_ID=""; FIXTURE_PER_ID=""; FIXTURE_IB_ID=""; FIXTURE_VIEW_ID=""
cleanup() {
  # Best-effort hard delete via admin (bypasses RLS) so a second consecutive
  # run starts clean. Deleting the person cascades to any events tied to it
  # (the manual fixture event below, and the RPC's own soft-delete event).
  [ -n "$FIXTURE_PER_ID" ] && run_sql "DELETE FROM people WHERE id='$FIXTURE_PER_ID'" >/dev/null 2>&1
  [ -n "$FIXTURE_COMP_ID" ] && run_sql "DELETE FROM companies WHERE id='$FIXTURE_COMP_ID'" >/dev/null 2>&1
  [ -n "$FIXTURE_IB_ID" ] && run_sql "DELETE FROM import_batches WHERE id='$FIXTURE_IB_ID'" >/dev/null 2>&1
  [ -n "$FIXTURE_VIEW_ID" ] && run_sql "DELETE FROM views WHERE id='$FIXTURE_VIEW_ID'" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

# Authenticate first so real rows can be seeded into every non-lookup table
# before the anon-denied loop runs -- an anon check against an empty table
# would pass trivially even with RLS fully disabled on it.
TOKEN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "{\"email\":\"$PB_TEST_EMAIL\",\"password\":\"$PB_TEST_PASSWORD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
AUTH=(-H "apikey: $ANON" -H "Authorization: Bearer $TOKEN")

BRAND_ID=$(curl -s "$BASE/rest/v1/brands?select=id&name=eq.gpt-trainer" "${AUTH[@]}" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')

FIXTURE_COMP_ID=$(curl -s -X POST "$BASE/rest/v1/companies" "${AUTH[@]}" -H "Content-Type: application/json" -H "Prefer: return=representation" \
  -d "{\"brand_id\":\"$BRAND_ID\",\"name\":\"_rls_test_co\",\"domain\":\"rls-test.example\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')

FIXTURE_PER_ID=$(curl -s -X POST "$BASE/rest/v1/people" "${AUTH[@]}" -H "Content-Type: application/json" -H "Prefer: return=representation" \
  -d "{\"brand_id\":\"$BRAND_ID\",\"company_id\":\"$FIXTURE_COMP_ID\",\"first_name\":\"RlsTest\",\"email\":\"rls-test-fixture@example.com\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')

curl -s -X POST "$BASE/rest/v1/events" "${AUTH[@]}" -H "Content-Type: application/json" \
  -d "{\"brand_id\":\"$BRAND_ID\",\"person_id\":\"$FIXTURE_PER_ID\",\"event_type\":\"note\",\"properties\":{\"note\":\"rls test fixture\"}}" >/dev/null

FIXTURE_IB_ID=$(curl -s -X POST "$BASE/rest/v1/import_batches" "${AUTH[@]}" -H "Content-Type: application/json" -H "Prefer: return=representation" \
  -d "{\"brand_id\":\"$BRAND_ID\",\"filename\":\"_rls_test.csv\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')

FIXTURE_VIEW_ID=$(curl -s -X POST "$BASE/rest/v1/views" "${AUTH[@]}" -H "Content-Type: application/json" -H "Prefer: return=representation" \
  -d "{\"brand_id\":\"$BRAND_ID\",\"name\":\"_rls_test_view\",\"object\":\"people\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')

# 1. anon alone must see nothing on ANY of the eight tables -- even though
#    every one of them now holds at least one real row (brands and
#    stage_options/event_types from the seed migrations, the rest from the
#    fixtures just inserted above).
for t in brands stage_options companies people event_types events import_batches views; do
  N=$(curl -s "$BASE/rest/v1/$t?select=*&limit=1" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else -1)')
  [ "$N" = "0" ] || { echo "FAIL: anon can read $t ($N)"; exit 1; }
done

# 2. authenticated user sees the seeded brand
N=$(curl -s "$BASE/rest/v1/brands?select=id&name=eq.gpt-trainer" "${AUTH[@]}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "$N" = "1" ] || { echo "FAIL: authenticated cannot read seeded brand ($N)"; exit 1; }

# 3. soft-delete round trip via the SECURITY DEFINER RPC: authenticated
#    cannot PATCH deleted_at directly (SELECT policy's USING is evaluated
#    against the NEW row on UPDATE), so this is the sanctioned path.
RESULT=$(curl -s -X POST "$BASE/rest/v1/rpc/soft_delete_person" "${AUTH[@]}" -H "Content-Type: application/json" \
  -d "{\"_id\":\"$FIXTURE_PER_ID\"}")
[ "$RESULT" = "true" ] || { echo "FAIL: soft_delete_person did not return true ($RESULT)"; exit 1; }

N=$(curl -s "$BASE/rest/v1/people?select=id&id=eq.$FIXTURE_PER_ID" "${AUTH[@]}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "$N" = "0" ] || { echo "FAIL: soft-deleted person still visible to authenticated select ($N)"; exit 1; }

echo "test_0004 OK"
