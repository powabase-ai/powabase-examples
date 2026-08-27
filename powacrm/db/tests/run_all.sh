#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for f in tests/test_0001_helpers.sql tests/test_0002_core_tables.sql tests/test_0003_events.sql \
         tests/test_0006_import_rpc.sql tests/test_0007_import_company_by_name.sql \
         tests/test_0011_research_schema.sql tests/test_0012_worker_rpcs.sql \
         tests/test_0013_worker.sql tests/test_0014_research_cap_bound.sql; do
  ./apply.sh "$f"
done

# ---------------------------------------------------------------------------
# Pause the live research worker for the HTTP tests below.
#
# 0013 schedules `powacrm-research-tick` to claim a queued job every minute,
# and it works on the queue PROJECT-WIDE. The SQL tests above are each a single
# transaction, so their fixtures are never visible to it. The HTTP tests are
# not: test_0012_request_research.sh creates real queued jobs over PostgREST
# and commits them, and the worker would happily claim one, call the agent on a
# fixture domain that does not exist, and spend platform credits on it.
#
# Paused here rather than before test_0013_worker.sql because that test asserts
# the cron job is scheduled and active -- which is the thing 0013 is supposed to
# produce.
#
# The restore is the part worth being careful about, and it is careful about
# three things. It restores the state the job was ACTUALLY in, not `true`:
# turning research on because someone ran the tests would be its own bug, and
# `absent` (0013 not applied) is left alone entirely. It runs from a trap, so a
# test failing partway through still puts the worker back. And if the restore
# itself fails it exits non-zero, because a suite that prints ALL DB TESTS OK
# while leaving research permanently switched off is precisely the silent
# success this project keeps getting bitten by.
# ---------------------------------------------------------------------------
psql_q() {
  # Quiet, unaligned, tuples-only. The SQL goes in argv (it holds no secrets);
  # the connection URL carries the database password, so it goes in via -e,
  # same reasoning as db/apply.sh.
  if command -v psql >/dev/null 2>&1; then
    psql "$PB_DB_URL" -v ON_ERROR_STOP=1 -qtAX -c "$1"
  else
    docker run --rm -i -e PGURL="$PB_DB_URL" -e SQL="$1" postgres:16-alpine \
      sh -c 'exec psql "$PGURL" -v ON_ERROR_STOP=1 -qtAX -c "$SQL"'
  fi
}
# Emits on/off/absent rather than casting the boolean: `active::text` is
# 'true'/'false', while psql's aligned DISPLAY of the same column is 't'/'f' --
# close enough to each other to write the wrong constant, which is exactly the
# mistake this function existed to survive, and did.
tick_active() { psql_q "SELECT coalesce((SELECT CASE WHEN active THEN 'on' ELSE 'off' END FROM cron.job WHERE jobname = 'powacrm-research-tick'), 'absent')" | tr -d '[:space:]'; }
set_tick() { psql_q "UPDATE cron.job SET active = $1 WHERE jobname = 'powacrm-research-tick'" >/dev/null; }

TICK_PRIOR=$(tick_active)
restore_tick() {
  status=$?
  case "$TICK_PRIOR" in
    on|off)
      [ "$TICK_PRIOR" = "on" ] && want=true || want=false
      if ! set_tick "$want"; then
        echo "FAIL: could not restore the powacrm-research-tick cron job to active=$want." >&2
        echo "      Research is left in whatever state this run put it in -- fix it by hand:" >&2
        echo "      UPDATE cron.job SET active = $want WHERE jobname = 'powacrm-research-tick';" >&2
        exit 1
      fi
      ;;
  esac
  exit $status
}
trap restore_tick EXIT

case "$TICK_PRIOR" in
  on)     set_tick false; echo "paused cron job powacrm-research-tick for the HTTP tests (was active)" ;;
  off)    echo "cron job powacrm-research-tick was already inactive -- leaving it that way" ;;
  absent) echo "no powacrm-research-tick cron job on this project -- nothing to pause" ;;
  *)      echo "FAIL: could not read the powacrm-research-tick cron job state (got '$TICK_PRIOR')" >&2; exit 1 ;;
esac

./tests/test_0004_rls.sh
./tests/test_0009_access_control.sh
./tests/test_0010_import_batch_scope.sh
./tests/test_0012_request_research.sh
# Needs PB_SERVICE_KEY on top of the variables above, makes a real agent run
# (~30 s) and spends platform credits -- it is the only check that the
# researcher's tool allowlist and its injection resistance actually hold.
./tests/test_0012_injection.sh
echo "ALL DB TESTS OK"
