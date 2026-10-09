#!/bin/bash
# Replicate (almost) every incident scenario back-to-back so their broken
# states AND logs exist for investigation demos.
# Excluded: scenario-10-expired-hold (waits ~70s for a TTL) — run it alone.
# Usage: bash incident-scenarios/run-all.sh
set -uo pipefail
cd "$(dirname "$0")"
for s in 01-stuck-payment 02-esim-failure 03-leaked-hold 04-notification-failed \
         05-duplicate-notify 06-suspended-customer 07-stuck-retry 08-n-plus-one \
         09-promo-vanished 11-bulk-partial 12-fiber-noresource 13-dup-msisdn \
         14-expired-promo; do
  echo "================ $s ================"
  bash scenario-$s/setup.sh 2>&1 | tail -4
  echo
done
echo "Done. Investigate any ticket, e.g.:"
echo '  curl -X POST localhost:8090/api/investigate -H "Content-Type: application/json" \'
echo '    -d "{\"incident_text\":\"INC-10101 Order <number> stuck in PAYMENT_PENDING.\"}"'
