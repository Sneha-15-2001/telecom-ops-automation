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

Replicate everything at once (except scenario-10, which waits ~70s for a TTL):

```bash
bash incident-scenarios/run-all.sh
```

Every run writes real rows + real log lines (with fresh correlation IDs printed
by each script) — that stored evidence is what the Phase 14 investigator reads.

Evidence for each: correlated logs (`scripts/search-logs.sh <correlationId>`),
DB rows, and the order/provisioning timeline APIs.
