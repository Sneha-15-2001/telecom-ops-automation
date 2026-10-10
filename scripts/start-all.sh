#!/bin/bash
# Start the entire stack: PostgreSQL, 5 Java services, both frontends,
# the BugZero API, and the log server.
#
#   bash scripts/start-all.sh      # bring everything up
#   bash scripts/stop-all.sh       # bring it all down
#   bash scripts/status.sh         # what is running
#
# Every service is started from the repo root on purpose. LOG_PATH defaults to
# a RELATIVE "logs" directory, so a service started from inside its own folder
# writes to a second, orphaned log directory and the cross-service trace
# silently breaks. This script is the safe way to run them.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PRODUCT="${PRODUCT_REPO:-$ROOT/../telecom-order-provisioning}"
LOGS="${LOG_PATH:-$PRODUCT/logs}"

export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}"
export PATH="$JAVA_HOME/bin:$PATH"
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_PORT="${POSTGRES_PORT:-5433}"
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
export LOG_PATH="$LOGS"
export PRODUCT_REPO_ROOT="$PRODUCT"

# Chaos fault injection, needed by incident scenarios 15, 16 and 21.
export CHAOS_ENABLED="${CHAOS_ENABLED:-true}"

PG_BIN=/Library/PostgreSQL/18/bin
PG_DATA="$HOME/.local/share/telecom-pg"

SERVICES="customer-service order-service inventory-service provisioning-service notification-service"

wait_for() {  # wait_for <url> <label> <seconds>
  local url=$1 label=$2 limit=${3:-60} i=0
  while [ $i -lt "$limit" ]; do
    curl -s -m 2 "$url" >/dev/null 2>&1 && { echo "  $label ready"; return 0; }
    sleep 2; i=$((i+2))
  done
  echo "  $label TIMED OUT — check /tmp/$label.log"
  return 1
}

echo "== PostgreSQL :$POSTGRES_PORT =="
if $PG_BIN/pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" 2>/dev/null | grep -q accepting; then
  echo "  already up"
else
  $PG_BIN/pg_ctl -D "$PG_DATA" -l "$PG_DATA.log" start >/dev/null 2>&1
  sleep 4
  $PG_BIN/pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" && echo "  started" || echo "  FAILED"
fi

echo "== Java services =="
cd "$PRODUCT" || { echo "product repo not found at $PRODUCT"; exit 1; }
for svc in $SERVICES; do
  jar="$svc/target/$svc-0.0.1-SNAPSHOT.jar"
  if [ ! -f "$jar" ]; then
    echo "  $svc — no jar yet (run scripts/build.sh)"
    continue
  fi
  # Always launched from the repo root so LOG_PATH resolves identically.
  nohup java -jar "$jar" > "/tmp/$svc.log" 2>&1 &
  disown
  echo "  $svc starting (pid $!)"
done

declare -A PORT=( [customer-service]=8081 [order-service]=8082 [inventory-service]=8083 \
                  [provisioning-service]=8084 [notification-service]=8085 )
for svc in $SERVICES; do
  wait_for "http://localhost:${PORT[$svc]}/actuator/health" "$svc" 70
done

echo "== Frontends and AI =="
cd "$PRODUCT/frontend/telecom-order-ui"  && nohup npm start > /tmp/ng.log 2>&1 &
disown; echo "  order UI        :4200"
cd "$ROOT/ai-investigator/console"       && nohup npx ng serve --port 4201 > /tmp/bz-console.log 2>&1 &
disown; echo "  BugZero console :4201"
cd "$ROOT/ai-investigator/backend"       && nohup python3 -m uvicorn app.main:app --port 8090 > /tmp/ai-backend.log 2>&1 &
disown; echo "  BugZero API     :8090"
cd "$ROOT/ai-investigator/logserver"     && LOG_ROOT="$LOGS" nohup python3 server.py > /tmp/logserver.log 2>&1 &
disown; echo "  log server      :8899"

wait_for "http://localhost:4200" "order UI" 90
wait_for "http://localhost:4201" "bugzero console" 90
wait_for "http://localhost:8090/api/health" "bugzero API" 30

echo
echo "Everything is up."
echo "  Order UI        http://localhost:4200"
echo "  BugZero console http://localhost:4201"
echo "  BugZero API     http://localhost:8090/docs"
echo
echo "Check what is running:  bash scripts/status.sh"
echo "Shut it all down:      bash scripts/stop-all.sh"