#!/bin/bash
# Scenario 20 (INC-10120): a lifecycle action on an order that has already moved
# on. 409, not 400 — the request was valid, the world was in the wrong state.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10120-$(date +%H%M%S)"

O=$(new_order "$CID")
OID=$(echo "$O" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -s -m 8 -X POST $ORDER/api/orders/$OID/validate -H "X-Correlation-ID: $CID" > /dev/null

echo "--- first submit (legal from VALIDATED) ---"
curl -s -m 10 -o /dev/null -w "  submit A -> HTTP %{http_code}\n" -X POST $ORDER/api/orders/$OID/submit -H "X-Correlation-ID: $CID-a"
echo "--- second submit (order is now PAYMENT_PENDING) ---"
curl -s -m 10 -o /tmp/c20.$$ -w "  submit B -> HTTP %{http_code}\n" -X POST $ORDER/api/orders/$OID/submit -H "X-Correlation-ID: $CID-b"
python3 -c "
import json
d=json.load(open('/tmp/c20.$$'))
print('  ',d['error'],'|',d['message'])
print('   correlationId:',d['correlationId'])"
rm -f /tmp/c20.$$
echo "INC-10120 orderId=$OID correlationId=$CID"
echo "verify: 200 then 409 ORDER_STATE_INVALID; order ends PAYMENT_PENDING exactly once"
echo
echo "--- true concurrency (both requests in flight) ---"
echo "NOTE: CustomerOrder has no @Version, so a genuinely simultaneous write is"
echo "last-write-wins rather than rejected. Running both at once may return 200"
echo "twice — that is a separate optimistic-locking gap, not this scenario."
curl -s -m 10 -o /dev/null -w "  cancel from PAYMENT_PENDING -> HTTP %{http_code}\n" -X POST $ORDER/api/orders/$OID/cancel -H "X-Correlation-ID: $CID-c"
curl -s -m 10 -o /tmp/c20b.$$ -w "  cancel again -> HTTP %{http_code}\n" -X POST $ORDER/api/orders/$OID/cancel -H "X-Correlation-ID: $CID-d"
rm -f /tmp/c20b.$$
