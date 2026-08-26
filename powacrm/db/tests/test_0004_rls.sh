#!/usr/bin/env bash
set -euo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}"
BASE="$VITE_POWABASE_URL"; ANON="$VITE_POWABASE_ANON_KEY"

# 1. anon alone must see nothing
N=$(curl -s "$BASE/rest/v1/brands?select=id" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else -1)')
[ "$N" = "0" ] || { echo "FAIL: anon can read brands ($N)"; exit 1; }

# 2. authenticated user sees the seeded brand
TOKEN=$(curl -s "$BASE/auth/v1/token?grant_type=password" -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d "{\"email\":\"$PB_TEST_EMAIL\",\"password\":\"$PB_TEST_PASSWORD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
N=$(curl -s "$BASE/rest/v1/brands?select=id&name=eq.gpt-trainer" -H "apikey: $ANON" -H "Authorization: Bearer $TOKEN" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "$N" = "1" ] || { echo "FAIL: authenticated cannot read seeded brand ($N)"; exit 1; }
echo "test_0004 OK"
