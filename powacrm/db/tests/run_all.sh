#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for f in tests/test_0001_helpers.sql tests/test_0002_core_tables.sql tests/test_0003_events.sql \
         tests/test_0006_import_rpc.sql tests/test_0007_import_company_by_name.sql \
         tests/test_0011_research_schema.sql tests/test_0012_worker_rpcs.sql \
         tests/test_0013_worker.sql; do
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
# produce. The trap puts it back however this script exits, including a failure
# partway through.
# ---------------------------------------------------------------------------
TICK_SQL=$(mktemp)
set_tick() {
  printf "UPDATE cron.job SET active = %s WHERE jobname = 'powacrm-research-tick';\n" "$1" > "$TICK_SQL"
  ./apply.sh "$TICK_SQL" >/dev/null
}
trap 'set_tick true 2>/dev/null || echo "WARNING: could not re-enable the powacrm-research-tick cron job -- do it by hand" >&2; rm -f "$TICK_SQL"' EXIT
set_tick false
echo "paused cron job powacrm-research-tick for the HTTP tests"

./tests/test_0004_rls.sh
./tests/test_0009_access_control.sh
./tests/test_0010_import_batch_scope.sh
./tests/test_0012_request_research.sh
# Needs PB_SERVICE_KEY on top of the variables above, makes a real agent run
# (~30 s) and spends platform credits -- it is the only check that the
# researcher's tool allowlist and its injection resistance actually hold.
./tests/test_0012_injection.sh
echo "ALL DB TESTS OK"
