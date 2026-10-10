#!/bin/bash
# Scenario 22 (INC-10122): a client posts the wrong Content-Type. This one
# needs no fault injection — a misconfigured integration triggers it.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10122-$(date +%H%M%S)"

echo "--- POST JSON body declared as text/plain ---"
curl -s -m 10 -X POST $ORDER/api/orders \
  -H 'Content-Type: text/plain' -H "X-Correlation-ID: $CID" \
  -d '{"customerId":1,"customerNumber":"CUS-DEMO001","orderType":"NEW_CONNECTION","items":[]}'
echo
echo "--- DELETE on a GET-only collection ---"
curl -s -m 10 -X DELETE $ORDER/api/orders -H "X-Correlation-ID: $CID"
echo
echo "INC-10122 correlationId=$CID"
echo "verify: 415 UNSUPPORTED_MEDIA_TYPE and 405 METHOD_NOT_ALLOWED respectively"
