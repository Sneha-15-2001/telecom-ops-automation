#!/bin/bash
# Scenario 21 (INC-10121): two dependencies fail at once. inventory-service
# errors during fulfillment, so order-service reports 502 and compensates
# whatever it had already taken — the interesting part is the partial failure.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10121-$(date +%H%M%S)"

O=$(new_order "$CID")
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -s -m 8 -X POST $ORDER/api/orders/$OID/validate -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 8 -X POST $ORDER/api/orders/$OID/submit -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 8 -X POST $ORDER/api/orders/$OID/payments -H 'Content-Type: application/json' -d '{"amount":299.00,"method":"UPI"}' > /dev/null
curl -s -m 8 -X POST $ORDER/api/orders/$OID/payment/validate -H "X-Correlation-ID: $CID" > /dev/null

chaos_arm $INV "/api/inventory/available" 500 60
echo "--- fulfill with inventory-service failing ---"
curl -s -m 30 -X POST "$ORDER/api/orders/$OID/fulfill" -H "X-Correlation-ID: $CID"
echo
echo "INC-10121 orderId=$OID correlationId=$CID"
echo "verify: curl $INV/api/inventory/reservations/order/$OID  (expect none — nothing should be held)"
echo "cleanup: curl -X DELETE $INV/api/chaos/fault?path=*"
