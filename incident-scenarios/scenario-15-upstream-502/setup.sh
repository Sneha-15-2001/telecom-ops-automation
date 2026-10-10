#!/bin/bash
# Scenario 15 (INC-10115): customer-service returns 500; order-service must
# surface 502 Bad Gateway and leave the order untouched and retryable.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10115-$(date +%H%M%S)"

chaos_arm $CUST "/api/customers/1/validate" 500 45

O=$(new_order "$CID")
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "--- validate with customer-service returning 500 ---"
curl -s -m 20 -X POST $ORDER/api/orders/$OID/validate -H "X-Correlation-ID: $CID"
echo
echo "INC-10115 orderId=$OID correlationId=$CID"
echo "verify: curl $ORDER/api/orders/$OID/status   (expect CREATED — NOT FAILED; it must stay retryable)"
echo "cleanup: bash $(dirname "$0")/setup.sh does not auto-clear; run: curl -X DELETE $CUST/api/chaos/fault?path=*"
