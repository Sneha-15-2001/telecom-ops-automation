#!/bin/bash
# Scenario 19 (INC-10119): an unhandled exception. The caller must get a generic
# 500 with a correlation ID — never a stack trace — and the detail must be
# findable in the logs from that ID.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10119-$(date +%H%M%S)"

# Pathological value: a huge quantity makes the arithmetic path blow up inside
# request handling with nothing mapped to it.
echo "--- order with an extreme line item ---"
curl -s -m 15 -X POST $ORDER/api/orders \
  -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"customerId":1,"customerNumber":"CUS-DEMO001","orderType":"NEW_CONNECTION","items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":99999999999999,"unitPrice":299.00}]}'
echo
echo "INC-10119 correlationId=$CID"
echo "verify: grep the correlation ID in logs/order-service/app.log for the stack trace"
echo "verify: response body must NOT contain a stack trace, only the correlation ID"
