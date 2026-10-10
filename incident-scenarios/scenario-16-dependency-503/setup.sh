#!/bin/bash
# Scenario 16 (INC-10116): order-service itself refuses with 503.
# In production this is what a drained pool, a full circuit breaker or a
# dependency-down guard returns. Reproduced here with the dev chaos switch.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10116-$(date +%H%M%S)"

chaos_arm $ORDER "/api/orders" 503 45

echo "--- orders endpoint while order-service is refusing ---"
curl -s -m 20 "$ORDER/api/orders?size=2"
echo
echo "INC-10116 correlationId=$CID"
echo "verify: curl $ORDER/api/chaos/status   (expect /api/orders armed with status 503)"
echo "cleanup: curl -X DELETE $ORDER/api/chaos/fault?path=*"
