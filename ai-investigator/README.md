# BugZero — Intelligent Incident Investigator

Turns an application issue into an evidence-backed diagnosis with a clear,
actionable fix, then hands it to the developer who can close it.

**Stack:** Angular console + Python/FastAPI investigation API. Reads the target
application's logs and databases read-only; writes only to Jira, to email, and
to BugZero's own history database.

```
ai-investigator/
├── backend/        FastAPI :8090 — collection, correlation, reasoning, handoff
├── console/        Angular 22 console :4200 — the operator experience
├── frontend-react/ superseded React console, kept for reference
└── logserver/      optional local read-only log HTTP server (:8899)
```

## Run

```bash
# 1. backend :8090
cd ai-investigator/backend
pip install -r requirements.txt
cp .env.example .env          # then fill what you need — every block is optional
python3 -m uvicorn app.main:app --port 8090     # docs at /docs

# 2. console :4200
cd ai-investigator/console
npm install && npm start      # → http://localhost:4200
```

Point it at the target application:

```bash
export MAIN=/path/to/telecom-order-provisioning
export INVESTIGATOR_LOG_ROOT=$MAIN/logs          # local log source
export PRODUCT_REPO_ROOT=$MAIN                  # grounds code fixes in real file:line
export POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres
```

`GET /api/health` reports every dependency and whether it is reachable. Nothing
is mandatory: with no configuration at all the console still runs, explains what
is missing, and renders the artifacts it would have sent.

## The pipeline

| Stage | What happens | Module |
|---|---|---|
| 1 · Jira issue input | Pull the real ticket, or accept free text | `jira_client`, `extractor` |
| 2 · Retrieve logs | Targeted search across SSH / HTTP / local sources | `logsearch`, `ssh_collector` |
| 3 · Analyze code | Locate the real class and method in the source tree | `codeindex` |
| 4 · AI analysis engine | Reason over the collected evidence | `chains` → `llmreason` |
| 5 · Developer output | Package evidence into a Jira ticket and email | `handoff`, `jira_client`, `mailer` |

Every `/api/investigate` response carries a `stages` array recording what each
stage actually found, which is what the console's timeline renders. A stage that
found nothing reports `warn` rather than a green tick.

## How evidence is gathered

1. **Extract** — regex pulls INC/order/customer/request/reservation/notification
   IDs, timestamps, status words and error hints from the ticket.
2. **Resolve** — order numbers → numeric IDs via read-only SELECT.
3. **Search** — targeted grep only, never whole files: qualified `orderId=` /
   `customerId=` with digit boundaries, so `orderId=2` never matches `orderId=21`.
   Rotation-safe (`.gz` archives included) and reads rotated logs so evidence
   cannot disappear.
4. **Correlate** — harvest correlation IDs, expand to full cross-service traces.
5. **Journey** — ordered service → event → status trail.
6. **DB evidence** — read-only SELECTs per identifier across the service databases.

## Log sources

Tried in order, chosen by configuration:

| Source | Enable with | Use |
|---|---|---|
| SSH | `BUGZERO_SSH_HOST` (+ key) | production hosts, via Paramiko — read-only SFTP |
| HTTP | `INVESTIGATOR_LOG_PULL` | the local log server on `:8899` |
| File | `INVESTIGATOR_LOG_ROOT` | default; the services' rolling logs |

SSH is read-only by construction: it opens SFTP, never execs a command and never
writes. Streams the tail with a byte cap so a busy production log is never
pulled whole. Set `BUGZERO_SSH_KNOWN_HOSTS` to refuse unknown host keys.

## LLM reasoning

The deterministic pipeline stays the evidence collector — verifiable, offline,
free. The model only reasons on top of what was collected.

```bash
cp .env.example .env   # fill ONE provider block, restart
```

OpenAI, Gemini (OpenAI-compatible endpoint), Meta, or local Ollama. With
`langchain-core` + `langchain-openai` installed, `chains.py` runs an LCEL chain
with schema-validated output; without them it falls back to the stdlib urllib
transport. The console's status bar always shows which engine is live.

The model is instructed to copy class and method names from the live catalog
`codeindex.catalog()` produces, so it cannot invent a method that does not exist.
`main.py` re-validates every draft against the source and repairs it once if the
model drifts — an unverified location is never shown as fact.

## Developer handoff

The step that closes the loop.

- **Preview** builds the ticket and the email and shows them, transmitting nothing.
- **Hand off** creates the Jira issue (with the evidence, proof lines, verified
  code location, proposed before/after) and emails the same pack to the owner.

Design choices worth knowing:

- Jira is created first so the email can cite the ticket key.
- Low-confidence findings are capped at Medium priority and labelled `needs-review`.
- The fix ticket is linked back to the source incident.
- With Jira or SMTP unconfigured, the artifacts are still rendered so an
  operator can paste them in by hand — degraded, never silent.

## History

BugZero's own database (`BUGZERO_DB_NAME`, default `bugzero`) — never the
`telecom_*` service schemas.

```bash
createdb bugzero
curl -X POST http://localhost:8090/api/setup/schema
```

Tables: `investigation` (what ran, what it concluded), `handoff` (which
channels succeeded), `followup` (the developer's outcome). The console's History
view reads them. If PostgreSQL is down, investigations still return their
evidence and say that history was not saved.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | dependency status |
| POST | `/api/investigate` | investigate free text |
| POST | `/api/investigate/jira` | fetch a ticket, then investigate it |
| GET | `/api/jira/issues` | ticket queue for the picker |
| GET | `/api/jira/issue` | one issue |
| POST | `/api/handoff` | build + transmit ticket and email |
| POST | `/api/handoff/dry-run` | build only, transmit nothing |
| POST | `/api/handoff/comment` | post follow-up evidence to a ticket |
| GET | `/api/history` | investigation history + stats |
| GET | `/api/history/detail` | one investigation with its evidence pack |
| POST | `/api/history/followup` | record the developer's outcome |
| POST | `/api/rca` | structured RCA + markdown |
| POST | `/api/datafix` / `/api/codefix` | fix proposals |
| GET | `/api/catalog` | live class/method catalog |
| POST | `/api/setup/schema` | create BugZero's tables |

## Safety

Reads are SELECTs over the target application's databases. The only writes go to
Jira, to email, and to BugZero's own `bugzero` database. Nothing commits,
deploys, or alters application data — every fix is a proposal a human reviews.