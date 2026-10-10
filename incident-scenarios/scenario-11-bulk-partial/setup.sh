#!/bin/bash
# Scenario 11 (INC-10111): corporate bulk order partially fails — 2 succeed, 1 bad.
set -uo pipefail
B2=http://localhost:8082
CID="INC-10111-$(date +%H%M%S)"
curl -s -m 10 -X POST $B2/api/orders/bulk -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"orders":[
    {"customerId":1,"orderType":"BULK","items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":1,"unitPrice":299.00}]},
    {"customerId":2,"orderType":"BULK","items":[{"itemType":"DEVICE","productCode":"DEV-PX8","productName":"Pixel X8","quantity":1,"unitPrice":59999.00}]},
    {"customerId":null,"orderType":"BULK","items":[]}
  ]}' | python3 -m json.tool
echo "INC-10111 correlationId=$CID (2 success + 1 item-error above)"
