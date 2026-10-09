#!/bin/bash
# Scenario 06 (INC-10106): order for a SUSPENDED customer fails validation (customer 3 is suspended in seed data).
set -uo pipefail
B2=http://localhost:8082
CID="INC-10106-$(date +%H%M%S)"
# Precondition (scenarios must establish, never assume, seed state):
curl -s -m 5 -X PATCH http://localhost:8081/api/customers/3/status \
  -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"status":"SUSPENDED"}' > /dev/null
O=$(curl -s -m 5 -X POST $B2/api/orders -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"customerId":3,"customerNumber":"CUS-DEMO003","orderType":"NEW_CONNECTION","items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":1,"unitPrice":299.00}]}')
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
ONUM=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['orderNumber'])")
curl -s -m 5 -X POST $B2/api/orders/$OID/validate -H "X-Correlation-ID: $CID" > /dev/null
echo "INC-10106 orderId=$OID orderNumber=$ONUM correlationId=$CID"
echo "verify: curl $B2/api/orders/$OID/status  (expect FAILED)"
echo "verify: history shows CUSTOMER_INVALID: [CUSTOMER_NOT_ACTIVE:SUSPENDED]"
