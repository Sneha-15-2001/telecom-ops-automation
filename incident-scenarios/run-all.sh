#!/bin/bash
# Replicate (almost) every incident scenario back-to-back so their broken
# states AND logs exist for investigation demos.
#
# Excluded on purpose:
#   scenario-10 — waits ~70s for a TTL to expire
#   scenario-18 — exhausts the rate limiter and needs ~60s to recover
#
# Scenario 18 is included last because it leaves the limiter tripped for a
# minute. Scenarios 15/16/21 arm chaos faults; each is cleared immediately
# after running so a later failure is not a leftover from an earlier one.
#
# Requires CHAOS_ENABLED=true on customer-service, order-service and
# inventory-service. Without it, scenarios 15/16/21 cannot inject faults.
set -uo pipefail
cd "$(dirname "$0")"

CLEAR_FAULTS() {
  for port in 8081 8082 8083 8084 8085; do
    curl -s -m 2 -X DELETE "http://localhost:$port/api/chaos/fault?path=*" >/dev/null 2>&1
  done
}

run() {
  echo "================ $1 ================"
  bash "scenario-$1/setup.sh" 2>&1 | tail -4
  echo
}

CLEAR_FAULTS
for s in 01-stuck-payment 02-esim-failure 03-leaked-hold 04-notification-failed \
         05-duplicate-notify 06-suspended-customer 07-stuck-retry 08-n-plus-one \
         09-promo-vanished 11-bulk-partial 12-fiber-noresource 13-dup-msisdn \
         14-expired-promo; do
  run "$s"
done

echo "================ platform-failure scenarios ================"
for s in 15-upstream-502 16-dependency-503 17-not-implemented-501 \
         19-unhandled-500 20-conflict-409 21-cascade-502 22-media-type-415; do
  run "$s"
  CLEAR_FAULTS
done

# Left out of the batch above because it trips the limiter for a full minute.
echo "================ 18-rate-limited-429 (NOT run) ================"
echo "Run it alone, then wait ~60s:  bash scenario-18-rate-limited-429/setup.sh"
echo

CLEAR_FAULTS
echo "Done. Investigate any ticket, e.g.:"
echo '  curl -X POST localhost:8090/api/investigate -H "Content-Type: application/json" \'
echo '    -d "{\"incident_text\":\"INC-10101 Order <number> stuck in PAYMENT_PENDING.\"}"'
echo '  curl -X POST localhost:8090/api/investigate -H "Content-Type: application/json" \'
echo '    -d "{\"incident_text\":\"INC-10115 order <id> validate returns 502 Bad Gateway.\"}"'
