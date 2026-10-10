#!/bin/bash
# Scenario 17 (INC-10117): an integrator calls a capability this deployment
# does not have. Real payment capture answers 501 rather than a fake success.
# No fault injection needed — the state is the point.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10117-$(date +%H%M%S)"

O=$(new_order "$CID")
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")

for cap in payment-capture network-activation refund; do
  echo "--- POST /api/unimplemented/orders/$OID/$cap ---"
  curl -s -m 10 -X POST "$ORDER/api/unimplemented/orders/$OID/$cap" -H "X-Correlation-ID: $CID"
  echo
done
echo "INC-10117 orderId=$OID correlationId=$CID"
echo "verify: all three return 501 NOT_IMPLEMENTED naming what must be integrated"
