# Telecom Ops Automation

The automation half of the Telecom Order Provisioning System — log tracing,
incident reproduction kits, and the AI investigator. Extracted so ops/AI work
lives separately from the Java + Angular product code.

**Main product repo:** `telecom-order-provisioning` (5 microservices + NexaTel UI).
This repo *drives* it; it does not replace it.

## What's inside

| Folder | What it automates |
|---|---|
| `scripts/` | `search-logs.sh <correlationId>` — trace one request across all services; `tail-all.sh` — watch everything live |
| `incident-scenarios/` | 14 Jira-style tickets, each with `setup.sh` (rebuilds the broken state) + `INC-*.md` card; `run-all.sh` replays 13 at once |
| `ai-investigator/backend/` | Python FastAPI: ticket → entities → log search → correlation → DB evidence → suspect → RCA, optional LLM reasoning |
| `ai-investigator/frontend/` | React console: ticket box, Jira queue, verdicts, journey, log lines, RCA download |
| `ai-investigator/logserver/` | Read-only log store on `127.0.0.1:8899` (store-on-localhost, pull over HTTP) |
| `sample-logs/` | A real traced fulfill (specimen output) |

## Wiring: point it at the product repo

Everything here reads the product repo's `logs/` and PostgreSQL. From this folder:

```bash
export MAIN=/path/to/telecom-order-provisioning
export LOG_PATH=$MAIN/logs                                    # scripts + log server
export INVESTIGATOR_LOG_ROOT=$MAIN/logs                       # investigator file mode
# or: export INVESTIGATOR_LOG_PULL=http://127.0.0.1:8899      # investigator pull mode
export POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres

# 1. reproduce incidents (needs the 5 Java services running — see product SETUP.md)
bash incident-scenarios/run-all.sh   # 13 scenarios; #10 (70s TTL wait) run alone

# 2. trace
./scripts/search-logs.sh <correlationId>

# 3. investigate
cd ai-investigator/backend && pip install -r requirements.txt
cp .env.example .env   # add LLM key for LLM mode (never commit .env)
python3 -m uvicorn app.main:app --port 8090

# 4. console + log server
cd ../frontend && npm install && npm run dev   # :5173
python3 ../logserver/server.py                 # 127.0.0.1:8899
```

Note: `incident-scenarios/` and `scripts/` are location-independent (absolute
service URLs + `LOG_PATH`). Only the investigator's *default* log path assumes
a sibling checkout — the env vars above override it.
