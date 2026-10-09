#!/bin/bash
# Tail all five service logs at once (Phase 12).
# Usage: ./scripts/tail-all.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_PATH:-$ROOT/logs}"
tail -F "$LOG_DIR"/*/app.log 2>/dev/null || echo "No log files yet in $LOG_DIR — start the services from the repo root first."
