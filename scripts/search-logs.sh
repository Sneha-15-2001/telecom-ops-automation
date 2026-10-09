#!/bin/bash
# Trace one request across all five services by correlation ID.
# Usage: ./scripts/search-logs.sh <correlationId> [orderId]
# Example: ./scripts/search-logs.sh e6289d1e-ae46-4a38-a406-dd869f4088ef
# The Phase 14 AI investigator automates exactly this search.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_PATH:-$ROOT/logs}"
PATTERN="${1:?correlation ID required}"

echo "=== correlationId=$PATTERN (logs: $LOG_DIR) ==="
for svc in customer-service order-service inventory-service provisioning-service notification-service; do
  f="$LOG_DIR/$svc/app.log"
  if [ -f "$f" ]; then
    echo "--- $svc ---"
    grep -a -h "$PATTERN" "$f" || echo "(no lines)"
  else
    echo "--- $svc --- (no log file yet — is the service running from the repo root?)"
  fi
done

if [ "${2:-}" != "" ]; then
  echo "=== orderId=$2 ==="
  for svc in customer-service order-service inventory-service provisioning-service notification-service; do
    f="$LOG_DIR/$svc/app.log"
    [ -f "$f" ] && grep -a -h "orderId=$2" "$f" || true
  done
fi
