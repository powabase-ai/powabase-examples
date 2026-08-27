#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for f in tests/test_0001_helpers.sql tests/test_0002_core_tables.sql tests/test_0003_events.sql \
         tests/test_0006_import_rpc.sql tests/test_0007_import_company_by_name.sql; do
  ./apply.sh "$f"
done
./tests/test_0004_rls.sh
echo "ALL DB TESTS OK"
