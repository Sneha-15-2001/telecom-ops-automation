#!/bin/bash
# Scenario 18 (INC-10118): order intake throttled. A runaway client or a
# double-clicking agent exhausts the budget and starts getting 429.
source "$(dirname "$0")/../_lib.sh"
CID="INC-10118-$(date +%H%M%S)"

echo "hitting order intake until it throttles (limit 120/min)..."
HIT=0
for i in $(seq 1 160); do
  code=$(curl -s -o /tmp/rl.$$ -w "%{http_code}" "$ORDER/api/orders?size=1")
  if [ "$code" = "429" ]; then HIT=$i; break; fi
done
echo "--- throttled at request #$HIT ---"
curl -s -m 10 -D /tmp/rlh.$$ "$ORDER/api/orders?size=1"
echo
grep -iE "retry-after|x-ratelimit" /tmp/rlh.$$ || true
cat /tmp/rl.$$; echo
rm -f /tmp/rl.$$ /tmp/rlh.$$
echo "INC-10118 correlationId=$CID throttledAtRequest=$HIT"
echo "verify: Retry-After header present, body error=RATE_LIMIT_EXCEEDED"
echo "note: wait ~60s for the window to reset before running other order scenarios"
