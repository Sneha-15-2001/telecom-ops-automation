#!/bin/bash
# Scenario 12 (INC-10112): fiber port reserved, but broadband activation fails —
# the provisioning request was opened without the resource number.
set -uo pipefail
B3=http://localhost:8083
B4=http://localhost:8084
CID="INC-10112-$(date +%H%M%S)"
# Self-sufficient: mint a fresh fiber port so reruns never depend on pool state.
STAMP=$(date +%H%M%S)
NEWR=$(curl -s -m 5 -X POST $B3/api/inventory/resources -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d "{\"resourceType\":\"FIBER_PORT\",\"identifier\":\"OLT9-PON9-PORT$STAMP\",\"details\":\"INC-10112 demo port\"}")
RID=$(echo "$NEWR" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
RNUM=$(curl -s -m 5 "$B3/api/inventory/resources/$RID" | python3 -c "import json,sys; print(json.load(sys.stdin)['resourceNumber'])")
S=$(curl -s -m 5 -X POST $B3/api/inventory/reserve -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d "{\"resourceId\":$RID,\"orderId\":7777,\"customerId\":1}")
SNUM=$(echo "$S" | python3 -c "import json,sys; print(json.load(sys.stdin)['reservationNumber'])")
# BUG REPLAY: agent opens broadband provisioning WITHOUT linking the reserved port.
P=$(curl -s -m 5 -X POST $B4/api/provisioning -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"orderId":7777,"customerId":1,"serviceType":"BROADBAND","planCode":"FIBER-200"}')
PID=$(echo "$P" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -s -m 5 -X POST $B4/api/provisioning/$PID/start -H "X-Correlation-ID: $CID" > /dev/null
curl -s -m 5 -X POST $B4/api/provisioning/$PID/activate -H "X-Correlation-ID: $CID" > /dev/null
echo "INC-10112 port=$RNUM($RID) held by $SNUM, provisioningId=$PID correlationId=$CID"
echo "verify: curl $B4/api/provisioning/$PID  (FAILED MISSING_RESOURCE while $RNUM sits reserved)"
