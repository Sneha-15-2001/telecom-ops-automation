#!/bin/bash
# Scenario 05 (INC-10105): duplicate notification — same event queued twice, customer spammed.
set -uo pipefail
B5=http://localhost:8085
CID="INC-10105-$(date +%H%M%S)"
for i in 1 2; do
  curl -s -m 5 -X POST $B5/api/notifications/notify -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
    -d '{"orderId":2,"customerId":1,"channel":"SMS","recipient":"+919876543210","event":"ORDER_COMPLETED","variables":{"orderNumber":"ORD-DUP","msisdn":"919876543210"}}' > /dev/null
done
echo "INC-10105 correlationId=$CID"
echo "verify: curl $B5/api/notifications/order/2  (expect 2+ ORDER_COMPLETED rows for the same event)"
