# AI Investigator (Phases 14–17, Phase 14 live)

Separate app with its own stack: **Python (FastAPI) backend + React (Vite) frontend**.
Reads the Java services' rolling logs + PostgreSQL databases. Strictly READ-ONLY.

## Run

```bash
# backend :8090 (needs POSTGRES_* env, same as the Java services)
cd ai-investigator/backend
pip install -r requirements.txt
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres \
  python3 -m uvicorn app.main:app --port 8090
# docs: http://localhost:8090/docs

# frontend :5173
cd ai-investigator/frontend
npm install && npm run dev   # → http://localhost:5173
```

## How it investigates (pipeline)

1. **Extract** — regex pulls INC/order/customer/request/reservation/notification IDs,
   timestamps, status words, error hints from the ticket text.
2. **Resolve** — order numbers → numeric IDs via read-only SELECT (logs carry `orderId=N`).
3. **Search** — targeted grep over `logs/*/app.log` (never whole files): qualified
   `orderId=`/`customerId=` matches with digit boundaries, so `orderId=2` never
   matches `orderId=21`.
4. **Correlate** — harvest correlation IDs, expand to full cross-service traces.
5. **Journey + suspect** — ordered service→event→status trail; most-specific ID sets
   the focus service; last FAILED event (or recorded-without-follow-up gap) names
   the suspect with confidence.
6. **DB evidence** — read-only SELECTs per identifier (order + history + payments +
   provisioning + reservations + notifications).

Response: entities, correlations, journey, log lines, DB evidence, suspect,
hypothesis, safety note. RCA text (15), data-fix (16), code-fix (17) build on this.

## LLM reasoning (optional, Phase 14b)

The deterministic pipeline stays as the evidence collector; an LLM reasons on
top when configured. No key = rule-based mode (fully working offline).

```bash
cd ai-investigator/backend
cp .env.example .env   # then fill ONE provider block
```

- OpenAI: `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_MODEL=gpt-4o-mini`, key `sk-…`
- Gemini: `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`,
  `LLM_MODEL=gemini-2.0-flash`, key `AIza…`
- Ollama (local, free): run `ollama pull llama3.1 && ollama serve`, then
  `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=llama3.1`, any dummy key.

Restart the backend. The UI shows an **LLM analysis** panel (root cause, fix
type, proposed SQL/code fixes, next steps) or the rule-based fallback notice.
`.env` is git-ignored — never commit keys.
