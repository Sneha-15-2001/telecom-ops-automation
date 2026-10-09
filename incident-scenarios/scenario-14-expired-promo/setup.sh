#!/bin/bash
# Scenario 14 (INC-10114): expired promo code applied — marketing promised it, system refuses.
set -uo pipefail
B2=http://localhost:8082
CID="INC-10114-$(date +%H%M%S)"
O=$(curl -s -m 5 -X POST $B2/api/orders -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"customerId":1,"orderType":"NEW_CONNECTION","items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":1,"unitPrice":299.00}]}')
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "--- applying EXPIRED5 (validity ended weeks ago): ---"
curl -s -m 5 -X POST $B2/api/orders/$OID/promotion/apply -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"promoCode":"EXPIRED5"}'; echo
echo "INC-10114 orderId=$OID correlationId=$CID (must 400 outside-validity-window)"
