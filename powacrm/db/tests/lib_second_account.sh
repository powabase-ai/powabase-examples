# ---------------------------------------------------------------------------
# The second account the isolation suites need -- created with the SERVICE KEY,
# not by public signup.
#
# WHY THIS FILE EXISTS. test_0009, test_0010 and test_0012 all need a second,
# unrelated account to prove that per-owner RLS actually separates two tenants.
# They used to create it by POSTing to /auth/v1/signup with the Anon key, and to
# `exit 0` with a SKIPPED line when the project refused. That made the coverage
# conditional on a setting -- and README's own security section tells operators
# to "turn signups off ... and treat that as required once research is enabled".
# So the configuration the docs mandate was exactly the one that switched off
# every test proving the authorization model, and run_all.sh still printed
# ALL DB TESTS OK. An operator hardened their fork, ran the suite, saw green, and
# had verified nothing about isolation.
#
# GoTrue's admin endpoint does not care about the signup policy: it is the
# service-role path db/setup/create_user.sh already uses to make the primary
# login. Using it here decouples what the tests prove from how the project is
# configured.
#
# Public signup is still exercised -- it is a supported feature and worth a
# regression test -- but only as a FALLBACK for builds whose admin API 404s, and
# a refusal there is now a loud exit 77 that run_all.sh reports, never a green
# `exit 0`.
#
# Usage, after PB_SERVICE_KEY / VITE_POWABASE_URL / VITE_POWABASE_ANON_KEY are
# checked and BASE is set:
#
#   . "$(dirname "$0")/lib_second_account.sh"
#   second_account "$B_EMAIL" "$B_PASSWORD" || exit $?
#   B_TOKEN="$SECOND_ACCOUNT_TOKEN"
#
# On success it sets SECOND_ACCOUNT_ID and SECOND_ACCOUNT_TOKEN. On a legitimate
# skip it prints a SKIPPED line and returns 77. On anything else it prints a
# FAIL line and returns 1.
# ---------------------------------------------------------------------------

SECOND_ACCOUNT_ID=""
SECOND_ACCOUNT_TOKEN=""

# Parses `id` from an admin-API user object or `user.id` from a signup response.
# Never fails the pipeline on a non-JSON body -- the caller reports the status.
_sa_user_id() {
  python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
if not isinstance(d, dict):
    print(""); raise SystemExit
print(d.get("id") or (d.get("user") or {}).get("id") or "")'
}

_sa_signup_disabled() {
  printf '%s' "$1" | grep -qiE 'signup(s)? (are |is )?(not allowed|disabled)|signup_disabled'
}

second_account() { # second_account <email> <password>
  local email="$1" password="$2" suite result code resp body_json login_body
  suite=$(basename "$0" .sh)
  # No apostrophe in the message: inside ${VAR:?word} bash parses the word with
  # quoting rules even within double quotes, so a lone ' opens a quote that never
  # closes and the whole file fails to parse when it is sourced.
  : "${PB_SERVICE_KEY:?PB_SERVICE_KEY is required: the isolation suites create their second account through the GoTrue admin API}"

  # Built with json.dumps rather than printf: a generated password containing a
  # double quote or a backslash produced malformed JSON and an opaque HTTP 400
  # in db/setup/create_user.sh, which is the script this path is modelled on.
  body_json=$(SA_EMAIL="$email" SA_PASSWORD="$password" python3 -c 'import json,os; print(json.dumps({"email": os.environ["SA_EMAIL"], "password": os.environ["SA_PASSWORD"], "email_confirm": True}))')

  result=$(curl -s -w '\n%{http_code}' "$BASE/auth/v1/admin/users" \
    -H "apikey: $PB_SERVICE_KEY" -H "Authorization: Bearer $PB_SERVICE_KEY" \
    -H "Content-Type: application/json" -d "$body_json")
  code="${result##*$'\n'}"; resp="${result%$'\n'*}"

  case "$code" in
    200|201)
      SECOND_ACCOUNT_ID=$(printf '%s' "$resp" | _sa_user_id)
      ;;
    404)
      # Older build with no admin API. Fall back to public signup, which is the
      # only remaining way to make an account -- and the one case where the
      # project's signup policy can still stop this suite.
      echo "$suite: admin API 404s on this Powabase build -- falling back to /auth/v1/signup"
      body_json=$(SA_EMAIL="$email" SA_PASSWORD="$password" python3 -c 'import json,os; print(json.dumps({"email": os.environ["SA_EMAIL"], "password": os.environ["SA_PASSWORD"]}))')
      result=$(curl -s -w '\n%{http_code}' "$BASE/auth/v1/signup" \
        -H "apikey: $VITE_POWABASE_ANON_KEY" -H "Content-Type: application/json" -d "$body_json")
      code="${result##*$'\n'}"; resp="${result%$'\n'*}"
      case "$code" in
        200|201) SECOND_ACCOUNT_ID=$(printf '%s' "$resp" | _sa_user_id) ;;
        *) if _sa_signup_disabled "$resp"; then
             echo "$suite SKIPPED: this build has no admin API and signups are disabled, so no second account can be created -- per-owner ISOLATION IS NOT VERIFIED by this run." >&2
             return 77
           fi
           echo "FAIL: $suite could not create its second account; the admin API 404ed and signup returned HTTP $code: $resp" >&2
           return 1 ;;
      esac
      ;;
    *)
      # A 422 here is NOT "already exists" unless the body says so: the emails
      # these suites generate are unique per run, so a 422 is far more likely to
      # be a password-policy rejection, and calling that success would surface
      # later as an unexplained RLS failure.
      if { [ "$code" = "422" ] || [ "$code" = "409" ]; } \
         && printf '%s' "$resp" | grep -qi "already registered\|already exists\|email_exists"; then
        SECOND_ACCOUNT_ID=$(printf '%s' "$resp" | _sa_user_id)
      else
        echo "FAIL: $suite could not create its second account via the admin API (HTTP $code): $resp" >&2
        return 1
      fi
      ;;
  esac

  # Built into a variable first, not inlined as a nested command substitution
  # inside the curl arguments: bash refuses to parse a `"$( ... )"` holding
  # double quotes inside another `$( ... )` here (`syntax error near unexpected
  # token`), and every caller of this file would have died at source time.
  login_body=$(SA_EMAIL="$email" SA_PASSWORD="$password" python3 -c 'import json,os; print(json.dumps({"email": os.environ["SA_EMAIL"], "password": os.environ["SA_PASSWORD"]}))')
  SECOND_ACCOUNT_TOKEN=$(curl -s "$BASE/auth/v1/token?grant_type=password" \
    -H "apikey: $VITE_POWABASE_ANON_KEY" -H "Content-Type: application/json" \
    -d "$login_body" \
    | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("access_token",""))
except Exception:
    print("")')
  if [ -z "$SECOND_ACCOUNT_TOKEN" ]; then
    echo "FAIL: $suite created its second account but could not sign in as it" >&2
    return 1
  fi
  return 0
}
