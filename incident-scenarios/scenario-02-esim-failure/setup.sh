#!/bin/bash
# Scenario 02 (INC-10102): eSIM provisioning fails — request opened without MSISDN.
set -uo pipefail
B4=http://localhost:8084
CID="INC-10102-$(date +%H%M%S)"
R=$(curl -s -m 5 -X POST $B4/api/provisioning -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"orderId":2,"customerId":1,"serviceType":"ESIM","planCode":"PLAN_5G_299"}')
RID=$(echo "$R" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
RNUM=$(echo "$R" | python3 -c "import json,sys; print(json.load(sys.stdin)['requestNumber'])")
curl -s -m 5 -X POST $B4/api/provisioning/$RID/start -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 5 -X POST $B4/api/provisioning/$RID/activate -H "X-Correlation-ID: $CID" > /dev/null
echo "INC-10102 requestId=$RID requestNumber=$RNUM correlationId=$CID"
echo "verify: curl $B4/api/provisioning/$RID  (expect FAILED + MISSING_MSISDN)"
