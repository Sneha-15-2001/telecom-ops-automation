#!/bin/bash
# Scenario 09 (INC-10109): promotion silently vanishes after order modify.
# Customer sees payable jump back up; support sees "no promo on order".
set -uo pipefail
B2=http://localhost:8082
CID="INC-10109-$(date +%H%M%S)"
O=$(curl -s -m 5 -X POST $B2/api/orders -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"customerId":1,"orderType":"NEW_CONNECTION","items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":1,"unitPrice":299.00}]}')
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
ONUM=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['orderNumber'])")
curl -s -m 5 -X POST $B2/api/orders/$OID/promotion/apply -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"promoCode":"FESTIVE10"}' > /dev/null
BEFORE=$(curl -s -m 5 $B2/api/orders/$OID -H "X-Correlation-ID: $CID" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['promoCode'], d['payableAmount'])")
curl -s -m 5 -X POST $B2/api/orders/$OID/modify -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":2,"unitPrice":299.00}]}' > /dev/null
AFTER=$(curl -s -m 5 $B2/api/orders/$OID -H "X-Correlation-ID: $CID" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['promoCode'], d['payableAmount'])")
echo "INC-10109 orderId=$OID orderNumber=$ONUM correlationId=$CID"
echo "before modify: $BEFORE"
echo "after modify:  $AFTER  (promo cleared, payable jumped)"
