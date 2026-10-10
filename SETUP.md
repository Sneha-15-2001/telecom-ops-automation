# Setup — Telecom Ops Automation

Incident scenarios, the BugZero investigator API, its Angular console, and the
log server.

> This repo *drives* `telecom-order-provisioning`; it does not replace it.
> You need the five Java services running before any of this is useful.

---

## Start everything at once

```bash
bash scripts/start-all.sh     # Postgres, 5 services, both UIs, BugZero, logserver
bash scripts/status.sh       # what is running + which dependencies are wired
bash scripts/stop-all.sh     # shut it all down
```

First-time only, before `start-all.sh` works:

```bash
cd ai-investigator/backend && pip install -r requirements.txt
cd ai-investigator/console  && npm install
```

---

## The three things you can run

| Port | What | Start it with |
|---|---|---|
| 8090 | BugZero investigation API | `python3 -m uvicorn app.main:app --port 8090` |
| 4201 | BugZero console (Angular) | `npx ng serve --port 4201` |
| 8899 | Log server (optional) | `LOG_ROOT=<product>/logs python3 server.py` |

All from `ai-investigator/`. The order UI lives in the other repo on :4200.

---

## Configuration

Everything is optional — the investigator runs with nothing configured and
tells you what is missing.

```bash
cd ai-investigator/backend
cp .env.example .env
```

`.env` is gitignored. The useful keys:

| Key | Why |
|---|---|
| `LLM_API_KEY` | Without it, no verdict or fix proposal — you get raw evidence |
| `INVESTIGATOR_LOG_ROOT` | Where the services write logs. Defaults to a path that is usually wrong; set it |
| `PRODUCT_REPO_ROOT` | Grounds code fixes in real file:line. Without it, locations are unverified |
| `POSTGRES_PORT` | `5433`, same as the services |
| `BUGZERO_DB_NAME` | History database, default `bugzero` |
| `JIRA_*` / `SMTP_*` | Handoff only; unconfigured means dry-run |

---

## History database (optional)

Stores what BugZero investigated. Separate from the service databases, so it
can never be confused with customer data.

```bash
createdb -h localhost -p 5433 -U postgres bugzero
curl -X POST http://localhost:8090/api/setup/schema
```

Without it, investigations still return evidence — the console just says
history was not saved.

---

## Incident scenarios

22 reproducible incidents: 14 domain failures, 8 HTTP/platform failures.

```bash
cd incident-scenarios
bash run-all.sh                              # 19 of them, ~2 min
bash scenario-01-stuck-payment/setup.sh      # or any one alone
```

| # | Ticket | Reproduces |
|---|---|---|
| 01 | INC-10101 | Payment recorded, never validated |
| 02 | INC-10102 | eSIM activation fails, missing MSISDN |
| 03 | INC-10103 | Reservation held forever |
| 04 | INC-10104 | Notification failed, customer uninformed |
| 05 | INC-10105 | Same event notified twice |
| 06 | INC-10106 | Order for a suspended customer |
| 07 | INC-10107 | Order parked in RETRYING |
| 08 | INC-10108 | N+1 queries on the order list |
| 09 | INC-10109 | Modify silently drops the promotion |
| 10 | INC-10110 | Past-TTL hold, resource stuck (70s wait) |
| 11 | INC-10111 | Bulk order, partial failure |
| 12 | INC-10112 | Fiber port reserved, activation fails |
| 13 | INC-10113 | MSISDN already in use |
| 14 | INC-10114 | Promotion outside its validity window |
| 15 | INC-10115 | 502 — customer-service errors during validation |
| 16 | INC-10116 | 503 — order-service refusing |
| 17 | INC-10117 | 501 — capability not implemented |
| 18 | INC-10118 | 429 — intake throttled (60s wait) |
| 19 | INC-10119 | 500 — unhandled exception |
| 20 | INC-10120 | 409 — lifecycle conflict |
| 21 | INC-10121 | 502 — inventory fails mid-saga |
| 22 | INC-10122 | 415 / 405 — malformed request |

Each folder has a Jira-style card (`INC-*.md`) explaining what the code means,
what to check, and whether it is a defect or working as designed.

**Three need chaos mode** — 15, 16 and 21. See
[`SETUP-QUICK.md`](../telecom-order-provisioning/SETUP.md) step 7, then always:

```bash
for p in 8081 8082 8083; do curl -s -X DELETE "http://localhost:$p/api/chaos/fault?path=*"; done
```

---

## Investigate

```bash
# free text
curl -X POST localhost:8090/api/investigate -H 'Content-Type: application/json' \
  -d '{"incident_text":"INC-10101 order stuck in PAYMENT_PENDING"}'

# a Jira ticket, if Jira is configured
curl -X POST localhost:8090/api/investigate/jira -H 'Content-Type: application/json' \
  -d '{"issue_key":"OPS-1234"}'

# ticket queue built from live database state
curl localhost:8090/api/incidents

# trace raw logs by correlation ID
./scripts/search-logs.sh <correlationId>
```

The console at :4201 does all of this with a pipeline timeline.

---

## ⚠️ The log path trap

`INVESTIGATOR_LOG_ROOT` and `LOG_PATH` default to a **relative** `logs`
directory. This repo has no `logs/` folder, so the defaults point at nothing
and every search returns zero lines while looking like a working system.

```bash
export INVESTIGATOR_LOG_ROOT=/Users/you/telecom-order-provisioning/logs
export LOG_PATH=$INVESTIGATOR_LOG_ROOT
```

If `search-logs.sh` reports "no log file yet" for all five services, this is
why. `scripts/status.sh` shows the log source BugZero actually resolved.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| "Investigator backend unreachable" in the console | :8090 not running |
| Investigation finds 0 log lines | `INVESTIGATOR_LOG_ROOT` wrong — see above |
| Verdict missing, evidence present | No `LLM_API_KEY`. Evidence still works |
| Code fix says "not verified" | `PRODUCT_REPO_ROOT` unset |
| `502`/`503` scenarios do nothing | `CHAOS_ENABLED=true` not set on that service |
| Console blank after backend edits | Restart :8090 — the `.env` is read at process start |
| History always empty | `bugzero` database missing or schema not created |
| Everything 429s | Scenario 18 tripped the limiter. Wait 60s |