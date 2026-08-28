#!/usr/bin/env bash
# Creates (or confirms) the single PowaCRM login user in GoTrue.
# Idempotent: an "already exists" response from either endpoint is treated
# as success -- the account existing is what matters, not who created it.
set -uo pipefail
: "${VITE_POWABASE_URL:?}" "${VITE_POWABASE_ANON_KEY:?}" "${PB_SERVICE_KEY:?}" "${PB_TEST_EMAIL:?}" "${PB_TEST_PASSWORD:?}"
BASE="$VITE_POWABASE_URL"

# Build the JSON properly. printf-interpolation produced malformed JSON (and an
# opaque HTTP 400) for any password containing a double quote or a backslash --
# entirely ordinary in a generated strong password.
admin_body=$(python3 -c 'import json,os; print(json.dumps({"email": os.environ["PB_TEST_EMAIL"], "password": os.environ["PB_TEST_PASSWORD"], "email_confirm": True}))')

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

if { [ "$code" = "422" ] || [ "$code" = "409" ]; } \
   && printf '%s' "$resp" | grep -qi "already registered\|already exists\|email_exists"; then
  echo "user already exists via admin API (HTTP $code) -- treating as success"
  exit 0
fi

# A 422 that does NOT say "already exists" is a different validation failure --
# weak_password is the common one. Treating it as success exited 0 having created
# nothing, and surfaced later as a KeyError on 'access_token' in another script.
if [ "$code" = "422" ]; then
  echo "FAIL: GoTrue rejected the request (HTTP 422): $resp"
  echo "      This is NOT 'already exists' -- check PB_TEST_PASSWORD meets the project's password policy."
  exit 1
fi

if [ "$code" = "404" ]; then
  echo "admin API 404s on this Powabase build -- falling back to /auth/v1/signup"
  signup_body=$(python3 -c 'import json,os; print(json.dumps({"email": os.environ["PB_TEST_EMAIL"], "password": os.environ["PB_TEST_PASSWORD"]}))')
  result=$(curl -s -w '\n%{http_code}' "$BASE/auth/v1/signup" \
    -H "apikey: $VITE_POWABASE_ANON_KEY" -H "Content-Type: application/json" \
    -d "$signup_body")
  code="${result##*$'\n'}"
  resp="${result%$'\n'*}"

  if [ "$code" = "200" ] || [ "$code" = "201" ]; then
    echo "user created via signup fallback (HTTP $code)"
    exit 0
  fi
  # Same body match as the admin branch above, and for the same reason. This
  # clause used to accept ANY 422 -- and, through a third `||` clause, any status
  # at all whose body happened to contain "already exists" -- as success. A
  # weak-password rejection therefore exited 0 having created nothing, and
  # resurfaced later as a KeyError on 'access_token' or an unexplained RLS test
  # failure. "Already exists" is the only 422 that means the account is there.
  if { [ "$code" = "422" ] || [ "$code" = "409" ]; } \
     && printf '%s' "$resp" | grep -qi "already registered\|already exists\|email_exists"; then
    echo "user already exists via signup fallback (HTTP $code) -- treating as success"
    exit 0
  fi
  if [ "$code" = "422" ]; then
    echo "FAIL: signup fallback rejected the request (HTTP 422): $resp"
    echo "      This is NOT 'already exists' -- check PB_TEST_PASSWORD meets the project's password policy."
    exit 1
  fi
  echo "FAIL: signup fallback returned HTTP $code: $resp"
  exit 1
fi

echo "FAIL: admin API returned HTTP $code: $resp"
exit 1
