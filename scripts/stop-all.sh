#!/bin/bash
# Stop the stack: 5 Java services (+ optionally PostgreSQL with --db).
# Run from the repo root: bash scripts/stop-all.sh [--db]
set -uo pipefail
pkill -f "java -jar .*(customer|order|inventory|provisioning|notification)-service" 2>/dev/null || true
pkill -f "java -jar" 2>/dev/null || true
sleep 3
left=$(ps aux | grep -c "[j]ava -jar" || true)
echo "java services remaining: $left"
if [ "${1:-}" = "--db" ]; then
  /Library/PostgreSQL/18/bin/pg_ctl -D ~/.local/share/telecom-pg stop 2>&1 | tail -1
fi
