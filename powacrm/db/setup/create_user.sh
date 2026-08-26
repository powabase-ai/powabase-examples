#!/usr/bin/env bash
# Creates (or confirms) the single PowaCRM login user in GoTrue.
# Idempotent: an "already exists" response from either endpoint is treated
# as success -- the account existing is what matters, not who created it.
set -uo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" "${PB_SERVICE_KEY:?}" "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}"
BASE="$VITE_POWABASE_URL"

admin_body=$(printf '{"email":"%s","password":"%s","email_confirm":true}' "$PB_TEST_EMAIL" "$PB_TEST_PASSWORD")

result=$(curl -s -w '\n%{http_code}' "$BASE/auth/v1/admin/users" \
  -H "apikey: $PB_SERVICE_KEY" -H "Authorization: Bearer $PB_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d "$admin_body")
code="${result##*$'\n'}"
resp="${result%$'\n'*}"

if [ "$code" = "200" ] || [ "$code" = "201" ]; then
  echo "user created via admin API (HTTP $code)"
  exit 0
fi

if [ "$code" = "422" ] || [ "$code" = "409" ]; then
  echo "user already exists via admin API (HTTP $code) -- treating as success"
  exit 0
fi

if [ "$code" = "404" ]; then
  echo "admin API 404s on this Powabase build -- falling back to /auth/v1/signup"
  signup_body=$(printf '{"email":"%s","password":"%s"}' "$PB_TEST_EMAIL" "$PB_TEST_PASSWORD")
  result=$(curl -s -w '\n%{http_code}' "$BASE/auth/v1/signup" \
    -H "apikey: $VITE_POWABASE_ANON_KEY" -H "Content-Type: application/json" \
    -d "$signup_body")
  code="${result##*$'\n'}"
  resp="${result%$'\n'*}"

  if [ "$code" = "200" ] || [ "$code" = "201" ]; then
    echo "user created via signup fallback (HTTP $code)"
    exit 0
  fi
  if [ "$code" = "422" ] || [ "$code" = "409" ] || printf '%s' "$resp" | grep -qi "already registered\|already exists"; then
    echo "user already exists via signup fallback (HTTP $code) -- treating as success"
    exit 0
  fi
  echo "FAIL: signup fallback returned HTTP $code: $resp"
  exit 1
fi

echo "FAIL: admin API returned HTTP $code: $resp"
exit 1
