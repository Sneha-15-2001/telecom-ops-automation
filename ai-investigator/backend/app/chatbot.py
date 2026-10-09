"""Conversational ops chatbot (vision: chatbot inside the Angular app).

POST /api/chat {message, mode} -> {reply, action, data}
Intents (rule-routed, deterministic):
  - reproduce / run INC-xxxxx  -> executes the scenario setup.sh (subprocess)
  - rca of <ticket>            -> full RCA document
  - status / health            -> live health of all services + UIs
  - anything with an ID        -> investigate
  - help / empty               -> capabilities
  - anything else              -> LLM free chat (llm mode) or guided hint (rules mode)
"""

import re
import subprocess

from . import dbcheck, investigator, rca as rca_builder

SCENARIO_RE = re.compile(r"INC-(\d{4,})", re.IGNORECASE)
RUN_WORDS = ("reproduce", "replicate", "run scenario", "trigger", "replay")
RCA_WORDS = ("rca", "root cause")
STATUS_WORDS = ("status", "health", "up?", "working", "alive")
HELP_WORDS = ("help", "what can you do", "capabilities", "hi", "hello", "hey")

SCENARIO_DIR = {
    "10101": "scenario-01-stuck-payment",
    "10102": "scenario-02-esim-failure",
    "10103": "scenario-03-leaked-hold",
    "10104": "scenario-04-notification-failed",
    "10105": "scenario-05-duplicate-notify",
    "10106": "scenario-06-suspended-customer",
    "10107": "scenario-07-stuck-retry",
    "10108": "scenario-08-n-plus-one",
    "10109": "scenario-09-promo-vanished",
    "10110": "scenario-10-expired-hold",
    "10111": "scenario-11-bulk-partial",
    "10112": "scenario-12-fiber-noresource",
    "10113": "scenario-13-dup-msisdn",
    "10114": "scenario-14-expired-promo",
}

SERVICES = {
    "customer-service": "http://localhost:8081/actuator/health",
    "order-service": "http://localhost:8082/actuator/health",
    "inventory-service": "http://localhost:8083/actuator/health",
    "provisioning-service": "http://localhost:8084/actuator/health",
    "notification-service": "http://localhost:8085/actuator/health",
    "ai-investigator": "http://localhost:8090/api/health",
    "log-server": "http://127.0.0.1:8899/api/health",
}


def _check_health() -> list:
    import json
    import urllib.request
    rows = []
    for name, url in SERVICES.items():
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                body = json.loads(resp.read().decode())
                status = body.get("status", "?")
        except Exception as e:
            status = f"DOWN ({type(e).__name__})"
        rows.append({"service": name, "status": status})
    return rows


def _run_scenario(inc: str):
    import os
    folder = SCENARIO_DIR.get(inc)
    if not folder:
        return None, f"I don't have a setup for INC-{inc} yet (known: {', '.join(sorted(SCENARIO_DIR))})."
    roots = [
        os.environ.get("SCENARIO_ROOT", ""),
        str(__import__("pathlib").Path(__file__).resolve().parents[3] / "incident-scenarios"),
        "/Users/sneha/telecom-ops-automation/incident-scenarios",
    ]
    script = None
    for r in roots:
        cand = os.path.join(r, folder, "setup.sh") if r else ""
        if cand and os.path.exists(cand):
            script = cand
            break
    if not script:
        return None, "Scenario files not found — set SCENARIO_ROOT to the incident-scenarios folder."
    try:
        proc = subprocess.run(["bash", script], capture_output=True, text=True, timeout=150)
        out = (proc.stdout + proc.stderr)[-2000:]
        return out, None
    except subprocess.TimeoutExpired:
        return None, "Scenario is still running (e.g. the 70s TTL wait) — check the services directly."
    except Exception as e:
        return None, f"Could not run it: {e}"


def chat(message: str, mode: str = "llm") -> dict:
    from . import extractor
    text = (message or "").strip()
    low = text.lower()
    if not text:
        return _help()
    entities = extractor.extract(text)
    has_id = dbcheck.has_identifiers(entities)

    m = SCENARIO_RE.search(text)
    if m and any(w in low for w in RUN_WORDS):
        out, err = _run_scenario(m.group(1))
        if err:
            return {"reply": err, "action": "run_scenario", "data": {"inc": m.group(1)}}
        return {"reply": f"Reproduced INC-{m.group(1)} — fresh broken state + logs stored. Output tail:\n{out}",
                "action": "run_scenario", "data": {"inc": m.group(1), "output": out}}

    if any(w in low for w in RCA_WORDS) or (m and "rca" in low):
        inv = investigator.investigate(text, mode=mode if mode in ("llm", "rules") else "llm")
        doc = rca_builder.build_rca(inv)
        return {"reply": (f"RCA {doc['incident']}: {doc['summary']} Root cause: {doc['root_cause']} "
                          f"(confidence {doc['confidence']}, fix {doc['fix_type']})."),
                "action": "rca", "data": {"rca": doc, "markdown": rca_builder.to_markdown(doc)}}

    if any(w in low for w in STATUS_WORDS) and not has_id:
        rows = _check_health()
        ups = [r for r in rows if r["status"] == "UP"]
        return {"reply": f"{len(ups)}/{len(rows)} services UP: " +
                         ", ".join(f"{r['service']}={r['status']}" for r in rows) + ".",
                "action": "status", "data": {"services": rows}}

    if any(w in low for w in HELP_WORDS) and not has_id:
        return _help()

    # default: full investigation (IDs present or not — investigator handles vague tickets)
    inv = investigator.investigate(text, mode=mode if mode in ("llm", "rules") else "llm")
    if inv.get("needs_info"):
        cands = [f"{r.get('order_number') or r.get('request_number') or r.get('notification_number') or r.get('reservation_number')}"
                 for rows in inv.get("candidates", {}).values() for r in rows[:2]]
        return {"reply": inv["message"] + " Fresh leads: " + ", ".join(c for c in cands if c and c != "None") + ".",
                "action": "investigate", "data": inv}
    llm = inv.get("llm") or {}
    analysis = llm.get("analysis") if llm.get("enabled") else None
    if analysis:
        verdict = (f"{analysis.get('summary','')} Root cause: {analysis.get('root_cause','')} "
                   f"(confidence {analysis.get('confidence','?')}, fix {analysis.get('fix_type','?')}).")
    else:
        verdict = inv.get("hypothesis", "")
    s = inv.get("suspect", {})
    return {"reply": f"{verdict} Suspect: {s.get('service')} ({s.get('signal')}).",
            "action": "investigate", "data": inv}


def _help() -> dict:
    return {"reply": ("I can: investigate tickets ('INC-10101 order ORD-… stuck'), write RCAs "
                      "('rca of …'), check system health ('is everything up?'), and reproduce "
                      "incidents ('reproduce INC-10104'). Dates optional — but give me an ID and the error."),
            "action": "help", "data": {}}
