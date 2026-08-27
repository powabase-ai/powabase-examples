#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for f in tests/test_0001_helpers.sql tests/test_0002_core_tables.sql tests/test_0003_events.sql \
         tests/test_0006_import_rpc.sql tests/test_0007_import_company_by_name.sql \
         tests/test_0011_research_schema.sql tests/test_0012_worker_rpcs.sql; do
  ./apply.sh "$f"
done
./tests/test_0004_rls.sh
./tests/test_0009_access_control.sh
./tests/test_0010_import_batch_scope.sh
./tests/test_0012_request_research.sh
# Needs PB_SERVICE_KEY on top of the variables above, makes a real agent run
# (~30 s) and spends platform credits -- it is the only check that the
# researcher's tool allowlist and its injection resistance actually hold.
./tests/test_0012_injection.sh
echo "ALL DB TESTS OK"
