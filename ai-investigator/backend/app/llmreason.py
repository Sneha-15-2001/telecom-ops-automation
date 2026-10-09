"""LLM reasoning layer (Phase 14b).

The deterministic pipeline (extractor + logsearch + dbcheck) stays as the
EVIDENCE COLLECTOR — verifiable, offline, free. The LLM acts only as the
REASONER on top: it receives the evidence JSON and returns a structured
analysis (root cause, fix type, proposed SQL/code fixes, next steps).

Transport is OpenAI-compatible (/chat/completions) over stdlib urllib, so the
same code works with OpenAI, Gemini's OpenAI endpoint, Ollama, OpenRouter, or
any compatible gateway — configured via .env, no new dependencies.
"""

import json
import os
import urllib.request

SYSTEM_PROMPT = """You are a senior telecom production-support engineer investigating an
order-provisioning incident across five microservices (customer, order,
inventory, provisioning, notification). You are given:

- the original ticket text,
- entities extracted deterministically (trust these IDs),
- a correlated cross-service log journey (chronological),
- the RAW correlated log lines themselves — grep these yourself, quote the
  exact lines that prove each link, and make YOUR OWN call on DATA vs CODE
  from what the lines show,
- read-only database evidence (current states).

Rules:
1. Ground every claim in the supplied evidence. Never invent log lines, IDs, or rows.
2. PLAIN LANGUAGE FIRST: write like you are explaining to a smart customer-care
   agent, not an engineer. Short sentences (under 20 words). No jargon — if a
   technical term is unavoidable, explain it in 5 words or less in brackets.
   Example: "The payment was recorded but never checked (checked = compared to the bill)."
3. Distinguish DATA issues (wrong stored information, a skipped step, a lapsed
   window) from CODE issues (a missing automatic check, a leak in the program,
   a slow query). Say which one in the first line of the summary.
4. Temporary data fixes are SQL PROPOSALS ONLY — always include a validation
   SELECT and a rollback note, and state that human approval is required.
5. Permanent code fixes name service/class/method and show before/after logic
   in plain words first, code second.
6. If evidence is thin, say so in one plain sentence and lower confidence; list
   exactly what to check next, simplest first.
7. evidence_refs must each QUOTE one raw log line (service + event + status)
   that proves that link — no uncited claims in the chain.

Reply with JSON ONLY, exactly this shape. Always provide BOTH data_fix_sql
AND code_fix (use null for one side only with a one-line reason inside
next_steps when it is genuinely inapplicable — e.g. a pure master-data state
needs no code change, a pure program bug needs no data change):
{
  "summary": "2-3 sentence incident summary",
  "root_cause": "single precise cause",
  "evidence_refs": ["log event / DB row supporting each link in the chain"],
  "confidence": "high|medium|low",
  "fix_type": "DATA|CODE|BOTH|UNKNOWN",
  "data_fix_sql": "proposal or null",
  "data_fix_validation": "SELECT to verify, or null",
  "data_fix_rollback": "rollback note, or null",
  "code_fix": {"service": "", "area": "", "before": "", "after": ""} or null,
  "next_steps": ["concrete checks, in order"]
}"""


def config() -> dict:
    """Read LLM settings from the environment (.env). Empty key => disabled."""
    _load_dotenv()
    return {
        "api_key": os.environ.get("LLM_API_KEY", "").strip(),
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        "timeout": int(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
    }


def _load_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line and line.split("=", 1)[0] not in os.environ:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("'\"")


def reason(incident_text: str, evidence: dict) -> dict:
    """Ask the LLM to reason over the deterministic evidence. Never raises:
    failures return {enabled, error} so the API always answers."""
    cfg = config()
    if not cfg["api_key"]:
        return {"enabled": False, "reason": "LLM_API_KEY not set — showing rule-based analysis only."}
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(
                {"ticket": incident_text, "evidence": evidence}, default=str)[:12000]},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        req = urllib.request.Request(
            cfg["base_url"] + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + cfg["api_key"]},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
            body = json.loads(resp.read().decode())
        content = body["choices"][0]["message"]["content"]
        return {"enabled": True, "model": cfg["model"], "analysis": json.loads(content)}
    except Exception as e:  # network, auth, bad JSON — degrade, don't 500
        return {"enabled": False, "reason": f"LLM call failed ({type(e).__name__}: {e}) — rule-based analysis shown."}
