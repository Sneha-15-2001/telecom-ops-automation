#!/bin/bash
# Start the whole stack in order: PostgreSQL -> 5 Java services.
# Run from the repo root: bash scripts/start-all.sh
# Env overrides: POSTGRES_HOST/PORT/USER/PASSWORD, JAVA_HOME
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_PORT="${POSTGRES_PORT:-5433}"
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}"
export PATH="$JAVA_HOME/bin:$PATH"

echo "== PostgreSQL :$POSTGRES_PORT =="
if /Library/PostgreSQL/18/bin/pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" 2>/dev/null | grep -q accepting; then
  echo "already up"
else
  /Library/PostgreSQL/18/bin/pg_ctl -D ~/.local/share/telecom-pg -l ~/.local/share/telecom-pg.log start
fi

echo "== microservices =="
cd "$ROOT"
for svc in customer-service order-service inventory-service provisioning-service notification-service; do
  port=$(grep -A1 "^server:" "$svc/src/main/resources/application.yml" | grep -oE "808[0-9]" | head -1)
  if curl -s -m 3 "http://localhost:$port/actuator/health" 2>/dev/null | grep -q '"status":"UP"'; then
    echo "$svc :$port already UP"
  else
    nohup java -jar "$svc/target/$svc-0.0.1-SNAPSHOT.jar" > "/tmp/$svc.log" 2>&1 &
    echo "$svc :$port starting (pid $!)"
  fi
done
echo "wait ~25s, then: for p in 8081 8082 8083 8084 8085; do curl -s http://localhost:$p/actuator/health | head -c 16; echo; done"

echo "== frontends + AI (background, logs in /tmp) =="
cd "$ROOT/frontend/telecom-order-ui" && nohup npm start > /tmp/ng.log 2>&1 & echo "angular :4200 (pid $!)"
cd "$ROOT/ai-investigator/frontend" && nohup npx vite --port 5173 --strictPort > /tmp/vite.log 2>&1 & echo "react :5173 (pid $!)"
cd "$ROOT/ai-investigator/backend" && POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  nohup python3 -m uvicorn app.main:app --port 8090 > /tmp/ai-backend.log 2>&1 & echo "ai-investigator :8090 (pid $!)"
cd "$ROOT/ai-investigator/logserver" && nohup python3 server.py > /tmp/logserver.log 2>&1 & echo "log-server 127.0.0.1:8899 (pid $!)"
echo "UIs need ~40s for first compile. Then open http://localhost:4200 (chat bubble bottom-right)."
