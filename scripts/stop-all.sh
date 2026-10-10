#!/bin/bash
# Stop everything start-all.sh started.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

for p in 4200 4201 8090 8899; do
  pid=$(lsof -ti:$p 2>/dev/null)
  [ -n "$pid" ] && kill $pid 2>/dev/null && echo "stopped :$p"
done

for p in 8081 8082 8083 8084 8085; do
  pid=$(lsof -ti:$p 2>/dev/null | head -1)
  [ -n "$pid" ] && kill $pid 2>/dev/null && echo "stopped :$p (Java service)"
done

# Any Java process still holding a built jar, in case a port moved.
pkill -f "telecom-order-provisioning/.*/target/.*\.jar" 2>/dev/null && echo "stopped leftover Java services"

echo "All stopped. PostgreSQL left running on purpose —"
echo "stop it with: /Library/PostgreSQL/18/bin/pg_ctl -D ~/.local/share/telecom-pg stop"
