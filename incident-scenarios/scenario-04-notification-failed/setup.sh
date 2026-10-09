#!/bin/bash
# Scenario 04 (INC-10104): notification fails — gateway rejects recipient, customer never informed.
set -uo pipefail
B5=http://localhost:8085
CID="INC-10104-$(date +%H%M%S)"
N=$(curl -s -m 5 -X POST $B5/api/notifications/notify -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"orderId":2,"customerId":1,"channel":"SMS","recipient":"+91fail-list","event":"ORDER_COMPLETED","variables":{"orderNumber":"ORD-X","msisdn":"919876543210"}}')
NID=$(echo "$N" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -s -m 5 -X POST $B5/api/notifications/$NID/send -H "X-Correlation-ID: $CID" > /dev/null
echo "INC-10104 notificationId=$NID correlationId=$CID"
echo "verify: curl $B5/api/notifications/$NID  (expect FAILED + SIMULATED_PROVIDER_REJECT)"
echo "verify: curl $B5/api/notifications/$NID/attempts  (attempt recorded)"
