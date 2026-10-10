#!/bin/bash
# Shared helpers for scenario setup scripts.
ORDER=http://localhost:8082
CUST=http://localhost:8081
INV=http://localhost:8083
PROV=http://localhost:8084
NOTIF=http://localhost:8085

# Chaos injection needs CHAOS_ENABLED=true on the target service; see the
# incident card. Arm a fault, or clear every armed fault.
chaos_arm()   { curl -s -m 5 -X POST "$1/api/chaos/fault?path=$2&status=$3&seconds=$4" >/dev/null; }
chaos_clear() { curl -s -m 5 -X DELETE "$1/api/chaos/fault?path=*" >/dev/null; }

# Create an order in CREATED state, ready for validate/submit.
new_order() {
  curl -s -m 8 -X POST $ORDER/api/orders \
    -H 'Content-Type: application/json' -H "X-Correlation-ID: $1" \
    -d '{"customerId":1,"customerNumber":"CUS-DEMO001","orderType":"NEW_CONNECTION","items":[{"itemType":"MOBILE_PLAN","productCode":"PLAN_5G_299","productName":"5G 299","quantity":1,"unitPrice":299.00}]}'
}

status_of() { curl -s -m 5 "$ORDER/api/orders/$1/status"; }
