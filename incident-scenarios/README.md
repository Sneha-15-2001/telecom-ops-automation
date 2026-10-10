# Incident Scenarios (Phase 13)

Reproducible, business-level telecom production incidents. Each scenario folder has:
- `setup.sh` — builds the broken state against local services (8081–8085), prints IDs
- `INC-xxxxx.md` — Jira-style incident card (input format for the Phase 14 AI investigator)

Run any scenario: `bash incident-scenarios/scenario-01-stuck-payment/setup.sh`

| Scenario | Incident | Broken state |
|---|---|---|
| 01-stuck-payment | INC-10101 | Payment recorded but never validated; order stuck PAYMENT_PENDING |
| 02-esim-failure | INC-10102 | eSIM provisioning FAILED (MISSING_MSISDN) |
| 03-leaked-hold | INC-10103 | Reservation ACTIVE forever; MSISDN blocked as RESERVED |
| 04-notification-failed | INC-10104 | Notification FAILED after gateway reject; customer uninformed |
| 05-duplicate-notify | INC-10105 | Same ORDER_COMPLETED event notified twice |
| 06-suspended-customer | INC-10106 | Order for SUSPENDED customer fails validation |
| 07-stuck-retry | INC-10107 | Order parked in RETRYING, never revalidated |
| 08-n-plus-one | INC-10108 | Order list slow: N+1 selects on order_item (perf) |
| 09-promo-vanished | INC-10109 | Modify silently drops promotion; payable jumps |
| 10-expired-hold | INC-10110 | Past-TTL confirm 400s forever; resource stuck RESERVED |
| 11-bulk-partial | INC-10111 | Corporate bulk: 2 created, 1 item rejected |
| 12-fiber-noresource | INC-10112 | Fiber port reserved, broadband FAILED (unlinked) |
| 13-dup-msisdn | INC-10113 | MSISDN refused: already with another customer |
| 14-expired-promo | INC-10114 | Advertised promo rejected: validity window passed |
| 15-upstream-502 | INC-10115 | customer-service 500 during validate → 502; order must stay retryable |
| 16-dependency-503 | INC-10116 | order-service refusing: 503 with the dependency named |
| 17-not-implemented-501 | INC-10117 | Payment capture / activation / refund answer 501 honestly |
| 18-rate-limited-429 | INC-10118 | Order intake throttled; Retry-After + X-RateLimit headers |
| 19-unhandled-500 | INC-10119 | Unmapped exception → generic 500 + correlation ID, no stack trace |
| 20-conflict-409 | INC-10120 | Re-submitting an already-submitted order → 409, not 400 |
| 21-cascade-502 | INC-10121 | inventory 500 mid-saga → 502, paid order preserved, no leaked hold |
| 22-media-type-415 | INC-10122 | Wrong Content-Type → 415; wrong verb → 405 (was 500) |

### Platform-failure scenarios (15–22)

These reproduce **HTTP-contract** failures rather than domain bugs, so you can
demonstrate that BugZero investigates infrastructure incidents and not just
order data problems.

Scenarios 15, 16 and 21 use the dev-only chaos switch, which must be enabled:

```bash
CHAOS_ENABLED=true java -jar customer-service/target/customer-service-0.0.1-SNAPSHOT.jar
# 15 needs it on customer-service (8081)
# 16 needs it on order-service   (8082)
# 21 needs it on inventory-service(8083)
```

Always clear armed faults afterwards — otherwise every later request to that
path fails:

```bash
curl -X DELETE "http://localhost:8081/api/chaos/fault?path=*"
```

Scenario 18 exhausts the rate limiter and takes ~60s to reset. Run it last, or
wait a minute before running other order scenarios.

Replicate everything at once (except scenarios 10 and 18, which wait on a TTL
and a rate-limit window):

```bash
bash incident-scenarios/run-all.sh
```

Every run writes real rows + real log lines (with fresh correlation IDs printed
by each script) — that stored evidence is what the Phase 14 investigator reads.

Evidence for each: correlated logs (`scripts/search-logs.sh <correlationId>`),
DB rows, and the order/provisioning timeline APIs.
