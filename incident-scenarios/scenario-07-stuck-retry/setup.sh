#!/bin/bash
# Scenario 07 (INC-10107): order parked in RETRYING — retried after a failure, never revalidated.
set -uo pipefail
B2=http://localhost:8082
CID="INC-10107-$(date +%H%M%S)"
O=$(curl -s -m 5 -X POST $B2/api/orders -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"customerId":2,"orderType":"DEVICE_ONLY","items":[{"itemType":"DEVICE","productCode":"DEV-PX8","productName":"Pixel X8","quantity":1,"unitPrice":59999.00}]}')
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
ONUM=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['orderNumber'])")
curl -s -m 5 -X POST $B2/api/orders/$OID/validate -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 5 -X POST $B2/api/orders/$OID/submit -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 5 -X POST $B2/api/orders/$OID/payments -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"amount":1.00,"method":"CARD"}' > /dev/null
curl -s -m 5 -X POST $B2/api/orders/$OID/payment/validate -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 5 -X POST $B2/api/orders/$OID/retry -H "X-Correlation-ID: $CID" > /dev/null
# NOTE: re-validation deliberately NOT called — the stuck state.
echo "INC-10107 orderId=$OID orderNumber=$ONUM correlationId=$CID"
echo "verify: curl $B2/api/orders/$OID/status  (expect RETRYING, no further movement)"
