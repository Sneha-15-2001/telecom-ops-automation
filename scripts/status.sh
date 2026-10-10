#!/bin/bash
# Show what is running and whether the BugZero dependencies are wired up.
printf "  %-6s %-16s %s\n" "PORT" "SERVICE" "STATE"
printf "  %-6s %-16s %s\n" "-----" "-------" "-----"

# Plain HTTP reachability. Anything that answers at all counts as up — the
# logserver returns JSON with no status field, which a stricter check misses.
check() {  # check <port> <name> <path>
  if curl -s -m 3 -o /dev/null "http://localhost:$1$3" 2>/dev/null; then
    printf "  %-6s %-16s %s\n" "$1" "$2" "UP"
  else
    printf "  %-6s %-16s %s\n" "$1" "$2" "DOWN"
  fi
}

if /Library/PostgreSQL/18/bin/pg_isready -h localhost -p 5433 2>/dev/null | grep -q accepting; then
  printf "  %-6s %-16s %s\n" "5433" "postgres" "UP"
else
  printf "  %-6s %-16s %s\n" "5433" "postgres" "DOWN"
fi

check 8081 customer-service /actuator/health
check 8082 order-service /actuator/health
check 8083 inventory-service /actuator/health
check 8084 provisioning-service /actuator/health
check 8085 notification-service /actuator/health
check 8090 bugzero-api /api/health
check 4200 order-ui /
check 4201 bugzero-console /
check 8899 logserver /api/files

echo
curl -s -m 4 http://localhost:8090/api/health 2>/dev/null | python3 -c "
import json, sys
try:
    s = json.load(sys.stdin)['sources']
except Exception:
    print('  BugZero API not responding — cannot read dependency status')
    sys.exit(0)

def state(d):
    # Each dependency reports readiness differently: some use 'reachable',
    # some 'configured', the log source just names a mode.
    for key in ('reachable', 'configured'):
        if key in d:
            return d[key]
    return d.get('mode') is not None

for name, d in s.items():
    print(f'  {name:9} {\"ready\" if state(d) else \"not configured\"}')
" 2>/dev/null
