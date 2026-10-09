#!/bin/bash
# Scenario 08 (INC-10108): N+1 query problem on order listing.
# OrderMapper loads items per order: 1 page query + N item queries.
# Proved by enabling Hibernate SQL debug via actuator, fetching one page,
# and counting the per-order item selects in the log file.
set -uo pipefail
B2=http://localhost:8082
CID="INC-10108-$(date +%H%M%S)"
# seed 10 orders fast via bulk (unique MSISDN-free items, no validation needed for list timing)
BULK='{"orders":['
for i in $(seq 1 10); do
  BULK="$BULK{\"customerId\":1,\"orderType\":\"PLAN_CHANGE\",\"items\":[{\"itemType\":\"ADDON\",\"productCode\":\"ADD-N1-$i\",\"productName\":\"Addon $i\",\"quantity\":1,\"unitPrice\":10.00}]},"
done
BULK="${BULK%,}]}"
curl -s -m 10 -X POST $B2/api/orders/bulk -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" -d "$BULK" > /dev/null
# turn on SQL debug just for the measurement, then off again
curl -s -m 5 -X POST $B2/actuator/loggers/org.hibernate.SQL -H 'Content-Type: application/json' -d '{"configuredLevel":"DEBUG"}' > /dev/null
BEFORE=$(grep -a -c "order_item oi1_0" /Users/sneha/telecom-order-provisioning/logs/order-service/app.log || true)
curl -s -m 10 "$B2/api/orders?page=0&size=15" -H "X-Correlation-ID: $CID" > /dev/null
sleep 1
AFTER=$(grep -a -c "order_item oi1_0" /Users/sneha/telecom-order-provisioning/logs/order-service/app.log || true)
curl -s -m 5 -X POST $B2/actuator/loggers/org.hibernate.SQL -H 'Content-Type: application/json' -d '{"configuredLevel":"WARN"}' > /dev/null
echo "INC-10108 correlationId=$CID"
echo "order_item selects for ONE page of 15: $((AFTER - BEFORE)) (expect ~15: 1 per order = N+1)"
echo "verify: grep -a -c 'order_item oi1_0' logs/order-service/app.log before/after a list call"
