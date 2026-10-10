#!/bin/bash
# Scenario 13 (INC-10113): IMEI/MSISDN already in use — second subscription blocked.
set -uo pipefail
B1=http://localhost:8081
CID="INC-10113-$(date +%H%M%S)"
echo "--- first subscription takes 919000007771: ---"
curl -s -m 5 -X POST $B1/api/customers/1/subscriptions -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"planCode":"PLAN_5G_299","planName":"5G 299","msisdn":"919000007771"}' | head -c 120; echo
echo "--- second subscription, same MSISDN for customer 2: ---"
curl -s -m 5 -X POST $B1/api/customers/2/subscriptions -H 'Content-Type: application/json' -H "X-Correlation-ID: $CID" \
  -d '{"planCode":"PLAN_5G_299","planName":"5G 299","msisdn":"919000007771"}'; echo
echo "INC-10113 correlationId=$CID (second call must 400 MSISDN already in use)"
