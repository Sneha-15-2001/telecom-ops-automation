"""Investigation orchestration (Phase 14).

Pipeline: incident text -> extract identifiers -> targeted log search ->
correlate -> journey -> suspect service + hypothesis. RCA text (15), data-fix
(16) and code-fix (17) build on this output in later phases.
"""

from . import dbcheck, extractor, llmreason, logsearch

# event -> owning service is known from the log line itself; these rules turn
# a journey into a suspect + hypothesis.
FAILED_MARKERS = ("FAILED",)


def _suspect(entities: dict, journey: list) -> dict:
    # Most specific identifier in the ticket sets the focus service.
    focus = None
    if entities.get("request_number"):
        focus = "provisioning-service"
    elif entities.get("reservation_number") or entities.get("resource_number"):
        focus = "inventory-service"
    elif entities.get("notification_number"):
        focus = "notification-service"
    pool = [j for j in journey if j["service"] == focus] if focus else journey

    failed = [j for j in (pool or journey)
              if j["status"] in FAILED_MARKERS or "FAIL" in j["event"]]
    if failed:
        last = failed[-1]
        return {
            "service": last["service"],
            "signal": f"{last['event']} status={last['status']}",
            "confidence": "high",
        }
    # stuck patterns: recorded step with no follow-up
    events = [j["event"] for j in journey]
    stuck_rules = [
        ("PAYMENT_RECORDED", "PAYMENT_VALIDATED", "order-service",
         "payment recorded but never validated — validation step skipped, not failed"),
        ("RESOURCE_RESERVED", "RESOURCE_ALLOCATED", "inventory-service",
         "resource reserved but never confirmed/released — leaked hold"),
        ("STATUS_CHANGED", None, None, None),
    ]
    for started, expected, svc, why in stuck_rules:
        if started in events and expected and expected not in events:
            return {"service": svc, "signal": f"{started} without {expected}", "confidence": "medium",
                    "hypothesis": why}
    if journey:
        return {"service": journey[-1]["service"],
                "signal": f"last observed: {journey[-1]['event']}",
                "confidence": "low"}
    return {"service": None, "signal": "no correlated log lines found", "confidence": "none"}


def _hypothesis(entities: dict, suspect: dict, db: dict) -> str:
    hints = entities.get("error_hints", [])
    if hints:
        joined = ", ".join(hints[:4])
    else:
        joined = "no clear error word in the ticket"
    orders = db.get("orders", [])
    state = f"order is now: {orders[0]['status']}" if orders else "order state unknown"
    svc = (suspect.get("service") or "unidentified service").replace("-service", "")
    signal = suspect.get("signal") or "no clear signal"
    return (f"Most likely trouble is in {svc}: {signal}. {state[0].upper() + state[1:]}. "
            f"Ticket hints: {joined}. "
            f"Confidence {suspect.get('confidence', '?')} — please check the timeline and records below.")


# Rough fix-type hint for rule mode (LLM refines it when enabled).
# Uses the suspect signal + ticket keywords + DB evidence, in that order.
DATA = "DATA (likely) — rule heuristic; enable LLM for a precise call"
CODE = "CODE (likely) — rule heuristic; enable LLM for a precise call"


def _fix_hint(suspect: dict, entities: dict, db: dict) -> str:
    signal = (suspect.get("signal") or "").upper()
    keywords = " ".join(entities.get("error_hints", [])).upper()
    statuses = " ".join(entities.get("status_words", [])).upper()
    text = " ".join([signal, keywords, statuses])

    # CODE: structural defects visible in signal/keywords first.
    if any(s in text for s in ("DUPLICATE", "N+1", "ROLLBACK")):
        return CODE
    if "LEAK" in text or ("EXPIRED" in text and ("RESERV" in text or "HOLD" in text)):
        return CODE + " — leaked/expired hold never compensated"
    if "WITHOUT RESOURCE_ALLOCATED" in text or "WITHOUT CONFIRM" in text:
        return CODE + " — reservation never confirmed/released"
    # DB evidence first — it beats keyword guessing.
    for p in db.get("provisioning", []):
        err = (p.get("last_error") or "").upper()
        if err.startswith("MISSING"):
            return DATA + " — activation missing its identifier"
    if any(p.get("status") == "FAILED" for p in db.get("payments", [])):
        return DATA + " — a payment attempt failed"
    if "SUSPENDED" in statuses or "BLOCKED" in statuses or "CUSTOMER" in signal:
        return DATA + " — customer/master-data state"
    if "WITHOUT" in signal or "EXPIRED" in text:
        return DATA + " — a step or validity window lapsed"
    if "NOTIFICATION" in signal or "SMS" in keywords:
        return DATA + " — recipient/template content"
    return "UNKNOWN — enable LLM for a precise call"


def investigate(incident_text: str, mode: str = "llm") -> dict:
    entities = extractor.extract(incident_text)
    if not dbcheck.has_identifiers(entities):
        # Vague ticket: don't guess — show what's missing + fresh leads to pick from.
        return {
            "incident_text": incident_text,
            "entities": entities,
            "needs_info": True,
            "message": ("No traceable ID found (need at least one of: order number/id, "
                        "customer number/id, request, reservation, notification or resource number). "
                        "Pick a lead below, or rephrase with an ID from the ticket."),
            "candidates": dbcheck.recent_candidates(),
            "correlations": [],
            "journey": [],
            "log_lines": [],
            "log_stats": {"direct_hits": 0, "lines": 0},
            "db_evidence": {},
            "suspect": {"service": None, "signal": "awaiting identifiers", "confidence": "none"},
            "hypothesis": "Not enough details to investigate yet.",
            "fix_hint": "UNKNOWN — provide an ID first",
            "mode": mode,
            "llm": {"enabled": False, "reason": "Skipped: ticket has no identifiers to ground on."},
            "safety": "READ-ONLY.",
        }
    entities = dbcheck.resolve_order_ids(entities)  # numbers -> ids before log search
    logs = logsearch.search(entities)
    db = dbcheck.collect(entities, logs)
    # Error signal required alongside the ID: an error token, a FAILED-ish status,
    # or DB state showing failure. Dates are never required.
    has_error = bool(entities.get("error_hints")) or any(
        w in entities.get("status_words", []) for w in
        ("FAILED", "CANCELLED", "EXPIRED", "SUSPENDED", "BLOCKED"))
    if not has_error:
        for rows in db.values():
            if isinstance(rows, list) and any(
                    str(r.get("status", "")).upper() in ("FAILED", "CANCELLED", "EXPIRED")
                    or str(r.get("last_error", "")) for r in rows if isinstance(r, dict)):
                has_error = True
                break
    suspect = _suspect(entities, logs["journey"])
    result = {
        "incident_text": incident_text,
        "entities": entities,
        "correlations": logs["correlations"],
        "journey": logs["journey"],
        "log_lines": logs["log_lines"],
        "log_stats": {"direct_hits": logs["direct_hits"], "lines": len(logs["log_lines"])},
        "db_evidence": db,
        "suspect": suspect,
        "hypothesis": _hypothesis(entities, suspect, db),
        "safety": "READ-ONLY: searched logs + SELECTs only. No data changed, nothing deployed.",
    }
    # LLM reasoner on top of the deterministic evidence (disabled without a key
    # or when the UI toggle selects rule mode).
    from . import logsearch as _ls
    result["fix_hint"] = _fix_hint(suspect, entities, db)
    result["has_error"] = has_error
    if not has_error:
        result["hypothesis"] += (" Note: ticket names an ID but describes no error — "
                                 "analysis is state-based only. Add the failure symptom for a sharper call.")
    result["log_sources"] = {
        "mode": "pull:" + _ls.LOG_PULL_BASE if _ls.LOG_PULL_BASE else "file",
        "root": str(_ls.LOG_ROOT),
        "files": [str(_ls.LOG_ROOT / s / "app.log") for s in _ls.SERVICES],
    }
    result["mode"] = mode
    if mode == "llm":
        result["llm"] = llmreason.reason(incident_text, {
            "entities": entities,
            "journey": logs["journey"],
            "suspect": suspect,
            "rule_hypothesis": result["hypothesis"],
            "db_evidence": db,
        })
    else:
        result["llm"] = {"enabled": False, "reason": "Rule mode selected in the UI toggle."}
    return result
