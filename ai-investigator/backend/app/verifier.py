"""Pre-flight verification: check the ticket's claims before trusting them.

A Jira ticket is a *claim*, not evidence. INC-10115 asserts "customer-service
is returning 500"; nothing had confirmed that before the investigator reasoned
about it. If customer-service were healthy, the old pipeline would still
produce a confident RCA — about an outage that was not happening.

So before diagnosing, this module interrogates the running system and reports
what the ticket said against what was actually observed. Three outcomes:

  CONFIRMED     the claim was observed — diagnose it
  CONTRADICTED   the system disagrees — say so loudly, do not invent a fix
  UNVERIFIABLE  the claim cannot be checked here — proceed, but say that

A contradicted ticket is the most valuable result this can produce, because it
turns a confident wrong answer into "that is not what is happening".
"""

import json
import re
import urllib.error
import urllib.request

# Named dependency -> port, matching the local five-service topology.
SERVICES = {
    "customer-service": 8081,
    "order-service": 8082,
    "inventory-service": 8083,
    "provisioning-service": 8084,
    "notification-service": 8085,
}

# A read-only business probe per service. Actuator health alone is not enough:
# a service can be perfectly healthy while its business endpoints refuse
# traffic, which is exactly the INC-10116 case. Probing only /actuator/health
# reported "healthy" and wrongly told the operator the ticket was stale.
PROBE = {
    "customer-service": "/api/customers?size=1",
    "order-service": "/api/orders?size=1",
    "inventory-service": "/api/inventory/resources?size=1",
    "provisioning-service": "/api/provisioning?size=1",
    "notification-service": "/api/notifications?size=1",
}

# Statuses that mean the service is up and serving, not answering.
UP_STATES = {"UP", "ACTIVE", "COMPLETED", "PENDING", "AVAILABLE"}


def _probe(port: int, path: str, timeout: float = 3.0) -> dict:
    """GET a read-only business endpoint. Never writes."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=timeout) as r:
            return {"ok": True, "http": r.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "http": e.code, "refused": e.code}
    except Exception as e:
        return {"ok": False, "http": None, "error": type(e).__name__}


def _service_state(service: str) -> dict:
    """Both signals: is the process alive, and is it serving business traffic?

    These disagree in a way that matters. Process alive + business refusing is
    a partial outage — the health endpoint is exempt from rate limiting and,
    here, from chaos injection, so it stays green while orders fail.
    """
    port = SERVICES[service]
    h = _health(port)
    process_up = bool(h.get("reachable")) and h.get("status") == "UP"
    p = _probe(port, PROBE[service])
    # 429 is throttling, not an outage: the service is up and refusing on purpose.
    serving = p.get("ok") or p.get("http") in (429,)
    return {
        "process_up": process_up,
        "serving": serving,
        "health": h.get("status"),
        "probe_http": p.get("http") or p.get("error"),
        "probe_path": PROBE[service],
    }


def _health(port: int, timeout: float = 2.0) -> dict:
    """GET /actuator/health on a dependency."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/actuator/health", timeout=timeout) as r:
            body = json.loads(r.read().decode())
            comps = body.get("components") or {}
            db = comps.get("db", {}).get("status")
            return {"reachable": True, "status": body.get("status"),
                    "db": db, "http": r.status}
    except urllib.error.HTTPError as e:
        return {"reachable": True, "status": f"HTTP_{e.code}", "db": None, "http": e.code}
    except Exception as e:
        return {"reachable": False, "status": None, "db": None,
                "error": type(e).__name__, "http": None}


def _claimed_services(text: str) -> list:
    """Services the ticket names as faulty."""
    low = text.lower()
    return [s for s in SERVICES if s in low]


def _claimed_codes(text: str) -> list:
    """HTTP status codes the ticket cites, as integers."""
    out = []
    for m in re.finditer(r"\b(?:HTTP\s*)?([1-5]\d{2})\b", text):
        code = int(m.group(1))
        if 400 <= code < 600 and code not in out:
            out.append(code)
    return out


def _service_in_status(code: int, service: str) -> bool:
    """What a given code would mean, had it happened to that service."""
    if code == 502 and service == "customer-service":
        return False   # 502 is the caller's status, not the callee's
    if code == 503 and service == "order-service":
        return True    # the service itself refusing is plausible
    return True


def _observed_codes(service: str, window_lines: int = 400) -> list:
    """Status codes that service actually emitted in its recent log."""
    from . import logsearch
    seen = []
    try:
        root = logsearch.LOG_ROOT / service / "app.log"
        if not root.exists():
            return []
        with open(root, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-window_lines:]
    except OSError:
        return []
    for line in tail:
        for m in re.finditer(r"\bstatus=(\d{3})\b", line):
            c = int(m.group(1))
            if c >= 400 and c not in seen:
                seen.append(c)
        for m in re.finditer(r"status=(\d{3})\s+(\w+)", line):
            if m.group(2) in ("FAILED", "EXPIRED", "CANCELLED") and 500 not in seen:
                pass  # domain status, not an HTTP code
    return seen


def _order_status(order_number: str) -> dict:
    """Ask order-service what an order's real status is."""
    try:
        with urllib.request.urlopen(
                f"http://localhost:8082/api/orders/number/{order_number}", timeout=3) as r:
            d = json.loads(r.read().decode())
            return {"found": True, "status": d.get("status"), "id": d.get("id")}
    except urllib.error.HTTPError as e:
        return {"found": False, "status": f"HTTP_{e.code}"}
    except Exception as e:
        return {"found": False, "status": None, "error": type(e).__name__}


def verify(text: str) -> dict:
    """Interrogate the running system against what the ticket claims.

    Never raises and never guesses: a check that cannot run reports
    UNVERIFIABLE with the reason, so the caller can say so honestly.
    """
    claims = []
    checks = []

    services = _claimed_services(text or "")
    codes = _claimed_codes(text or "")

    # 1. Are the services the ticket blames actually serving traffic?
    for svc in services:
        st = _service_state(svc)
        observed = (f"process health={st['health']}, "
                    f"business probe {st['probe_path']} -> {st['probe_http']}")
        checks.append({
            "check": f"{svc} serving",
            "expected": "named as faulty in the ticket",
            "observed": observed,
            "agrees": not st["serving"],
        })
        if st["serving"] and st["process_up"]:
            claims.append({
                "claim": f"{svc} is faulty",
                "verdict": "CONTRADICTED",
                "detail": f"{svc} is healthy AND serving business traffic "
                          f"({st['probe_path']} -> {st['probe_http']}).",
            })
        elif st["serving"] and not st["process_up"]:
            claims.append({
                "claim": f"{svc} is faulty",
                "verdict": "CONTRADICTED",
                "detail": f"{svc} reports health={st['health']} but the business probe "
                          f"answers ({st['probe_http']}).",
            })
        elif not st["serving"] and st["process_up"]:
            # The interesting one: alive but refusing orders.
            claims.append({
                "claim": f"{svc} is faulty",
                "verdict": "CONFIRMED",
                "detail": f"PARTIAL OUTAGE — {svc} is healthy "
                          f"(health={st['health']}) but its business endpoint "
                          f"{st['probe_path']} returns {st['probe_http']}. "
                          f"A green health check is misleading here.",
            })
        else:
            claims.append({
                "claim": f"{svc} is faulty",
                "verdict": "CONFIRMED",
                "detail": f"{svc} is not accepting connections "
                          f"(probe {st['probe_path']} -> {st['probe_http']}).",
            })

    # 2. Did the cited HTTP status actually occur?
    for code in codes:
        observed_any = []
        for svc in (services or list(SERVICES)):
            found = _observed_codes(svc)
            observed_any.extend((svc, c) for c in found)
        matching = [(s, c) for s, c in observed_any if c == code]
        if matching:
            claims.append({
                "claim": f"HTTP {code} occurred",
                "verdict": "CONFIRMED",
                "detail": f"{matching[0][0]} logged status={code} in its recent output.",
            })
        elif services:
            claims.append({
                "claim": f"HTTP {code} occurred",
                "verdict": "UNVERIFIABLE",
                "detail": f"No status={code} in the recent logs of "
                          f"{', '.join(services)}. Logs may have rotated, or the "
                          f"response came from a service the ticket did not name.",
            })

    # 3. Is the named order really in the state the ticket claims?
    for m in re.finditer(r"\b(ORD-[A-Z0-9]{4,})\b", text or "", re.I):
        num = m.group(1).upper()
        real = _order_status(num)
        claimed = None
        around = text[max(0, m.start() - 120): m.end() + 120].upper()
        for st in ("PAYMENT_PENDING", "FAILED", "RETRYING", "CANCELLED",
                   "COMPLETED", "VALIDATED", "INVENTORY_RESERVED", "CREATED"):
            if st in around:
                claimed = st
                break
        if real.get("found") and claimed:
            agrees = real.get("status") == claimed
            claims.append({
                "claim": f"{num} is in {claimed}",
                "verdict": "CONFIRMED" if agrees else "CONTRADICTED",
                "detail": (f"order-service reports {real['status']}."
                           if agrees else
                           f"Ticket says {claimed}; order-service reports "
                           f"{real['status']}."),
            })
        elif real.get("found") and not claimed:
            claims.append({
                "claim": f"{num} exists",
                "verdict": "CONFIRMED",
                "detail": f"order-service reports status={real['status']} (id={real.get('id')}).",
            })
        elif not real.get("found"):
            claims.append({
                "claim": f"{num} exists",
                "verdict": "UNVERIFIABLE" if not real.get("error") else "UNVERIFIABLE",
                "detail": f"order-service did not return it ({real.get('status') or real.get('error')}).",
            })

    if not claims:
        return {
            "checked": 0, "verdict": "UNVERIFIABLE",
            "summary": "The ticket makes no checkable claim (no service, status code or order named).",
            "claims": [], "checks": checks,
        }

    verdicts = [c["verdict"] for c in claims]
    if "CONTRADICTED" in verdicts:
        overall = "CONTRADICTED"
        summary = (f"{verdicts.count('CONTRADICTED')} of {len(claims)} checked claims "
                   f"disagree with the running system. Treat this ticket as stale.")
    elif "CONFIRMED" in verdicts:
        overall = "CONFIRMED"
        summary = f"{verdicts.count('CONFIRMED')} of {len(claims)} claims confirmed against live evidence."
    else:
        overall = "UNVERIFIABLE"
        summary = "No claim could be checked here; the investigation below is based on the ticket text alone."

    return {"checked": len(claims), "verdict": overall, "summary": summary,
            "claims": claims, "checks": checks}