#!/bin/bash
# Scenario 01 (INC-10101): payment recorded but never validated.
# The money flow stalls: payment row stays PENDING, order stuck PAYMENT_PENDING.
set -uo pipefail
B2=http://localhost:8082
CID="INC-10101-$(date +%H%M%S)"
O=$(curl -s -m 5 -X POST $B2/api/orders -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"customerId":1,"customerNumber":"CUS-DEMO001","orderType":"NEW_CONNECTION","items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":1,"unitPrice":299.00}]}')
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
ONUM=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['orderNumber'])")
curl -s -m 5 -X POST $B2/api/orders/$OID/validate -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 5 -X POST $B2/api/orders/$OID/submit -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 5 -X POST $B2/api/orders/$OID/payments -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"amount":299.00,"method":"UPI"}' > /dev/null
# NOTE: payment/validate deliberately NOT called — the stuck state.
echo "INC-10101 orderId=$OID orderNumber=$ONUM correlationId=$CID"
echo "verify: curl $B2/api/orders/$OID/status  (expect PAYMENT_PENDING)"
echo "verify: curl $B2/api/orders/$OID/payments  (payment PENDING, never validated)"
