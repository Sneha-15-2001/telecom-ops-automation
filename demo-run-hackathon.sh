#!/bin/bash
# Hackathon demo, ~5 minutes, fully live (Phase 18).
# Arc: order -> pay -> fulfill FAILS (injected) -> ticket -> chatbot investigate
#   -> RCA -> data-fix proposal -> code-fix proposal.
# Prereqs: 5 Java services + ai backend running (bash scripts/start-all.sh).
set -uo pipefail
B2=http://localhost:8082
AI=http://localhost:8090
say() { printf "\n\033[1;31m>>> %s\033[0m\n" "$1"; sleep 1; }

say "1/7 Customer orders broadband (order + pay)"
STAMP=$(date +%H%M%S)
curl -s -m 5 -X POST http://localhost:8083/api/inventory/resources -H 'Content-Type: application/json' \
  -d "{\"resourceType\":\"FIBER_PORT\",\"identifier\":\"OLT9-PON9-DEMO$STAMP\",\"details\":\"demo port\"}" > /dev/null
O=$(curl -s -m 5 -X POST $B2/api/orders -H 'Content-Type: application/json' \
  -d '{"customerId":1,"orderType":"BROADBAND","items":[{"itemType":"BROADBAND","productCode":"FIBER-200","productName":"Fiber 200Mbps","quantity":1,"unitPrice":499.00}]}')
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
ONUM=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['orderNumber'])")
for op in validate submit; do curl -s -m 5 -X POST $B2/api/orders/$OID/$op > /dev/null; done
curl -s -m 5 -X POST $B2/api/orders/$OID/payments -H 'Content-Type: application/json' -d '{"amount":499.00,"method":"CARD"}' > /dev/null
curl -s -m 5 -X POST $B2/api/orders/$OID/payment/validate > /dev/null
echo "order $OID ($ONUM) paid and ready"

say "2/7 Fulfillment hits a provisioning fault (injected)"
curl -s -m 40 -X POST "$B2/api/orders/$OID/fulfill?failAt=ACTIVATE" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('order status:', d['orderStatus'])
[print(' ', s['step'], s['status'], '-', s['detail'][:70]) for s in d['steps']]"

say "3/7 Support ticket lands"
TICKET="Broadband order $ONUM FAILED during activation for order $OID."
echo "Ticket: $TICKET"

say "4/7 Chatbot investigates"
curl -s -m 40 -X POST $AI/api/chat -H 'Content-Type: application/json' -d "{\"message\":\"$TICKET\",\"mode\":\"rules\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'][:400])"

say "5/7 RCA document"
curl -s -m 40 -X POST $AI/api/rca -H 'Content-Type: application/json' -d "{\"incident_text\":\"$TICKET\",\"mode\":\"rules\"}" \
  | python3 -c "import json,sys; r=json.load(sys.stdin)['rca']; print('root cause:', r['root_cause'][:160]); print('fix:', r['fix_type'][:60])"

say "6/7 Temporary data fix (proposal, human approves)"
curl -s -m 40 -X POST $AI/api/datafix -H 'Content-Type: application/json' -d "{\"incident_text\":\"$TICKET\",\"mode\":\"rules\"}" \
  | python3 -c "import json,sys; [print('-', f['title']) or [print('   ', s[:90]) for s in f['sql']] for f in json.load(sys.stdin)['fixes']]"

say "7/7 Permanent code fix (proposal, human merges)"
curl -s -m 40 -X POST $AI/api/codefix -H 'Content-Type: application/json' -d "{\"incident_text\":\"$TICKET\",\"mode\":\"rules\"}" \
  | python3 -c "import json,sys; [print('-', f['title'], '|', f['service']) for f in json.load(sys.stdin)['fixes']]"

say "Done — same flow, click by click, in the NexaTel Incidents page and the :5173 console."
