"""BugZero — Intelligent Incident Investigator API.

Run:  uvicorn app.main:app --port 8090   (from ai-investigator/backend)
Docs: http://localhost:8090/docs

Pipeline (slide 3):
  JIRA ISSUE INPUT -> RETRIEVE LOGS -> ANALYZE CODE -> AI ENGINE -> DEVELOPER OUTPUT
Every /api/investigate response carries a `stages` array so the console can
render that pipeline as it executes rather than showing a spinner and then a wall.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import (chatbot, chains, codefix, codeindex, datafix, handoff, investigator,
               jira_client, logsearch, mailer, rca as rca_builder, ssh_collector, store)

app = FastAPI(title="BugZero — Intelligent Incident Investigator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    # localhost and 127.0.0.1 are different origins to a browser. 4201 is the
    # BugZero console; 4200 is the order UI; 5173 is the legacy React console.
    allow_origins=["http://localhost:4201", "http://127.0.0.1:4201",
                   "http://localhost:4200", "http://127.0.0.1:4200",
                   "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvestigateRequest(BaseModel):
    incident_text: str
    mode: str = "llm"  # "llm" (default, falls back gracefully) or "rules"


class ChatRequest(BaseModel):
    message: str
    mode: str = "llm"


class JiraIssueRequest(BaseModel):
    issue_key: str
    mode: str = "llm"


class HandoffRequest(BaseModel):
    incident_text: str
    source_issue_key: str = None
    mode: str = "llm"
    create_ticket: bool = True
    send_email: bool = True
    to_addrs: list = None
    extra_labels: list = None


class FollowupRequest(BaseModel):
    reference: str
    note: str = ""
    new_status: str = None
    jira_key: str = None


@app.post("/api/verify")
def verify_claims(req: InvestigateRequest):
    """Stage 0 — check the ticket against the running system before diagnosing.

    A ticket is a claim. This reports what it said against what was actually
    observed, so a stale or fictional ticket is caught before it produces a
    confident-sounding RCA about an outage that is not happening.
    """
    from . import verifier
    return verifier.verify(req.incident_text)


def _stages(inv: dict) -> list:
    """Reconstruct the five-slide pipeline from a finished investigation.

    Built from what actually happened rather than hardcoded, so a stage that
    found nothing reports honestly instead of showing a green tick.
    """
    entities = inv.get("entities") or {}
    db = inv.get("db_evidence") or {}
    llm = inv.get("llm") or {}
    stats = inv.get("log_stats") or {}
    source = inv.get("log_sources") or {}

    def stage(n, name, detail, state, count=None):
        return {"n": n, "name": name, "detail": detail, "state": state, "count": count}

    id_count = sum(len(v) for k, v in entities.items()
                   if k in ("order_number", "customer_number", "request_number",
                            "reservation_number", "notification_number",
                            "resource_number", "order_id", "customer_id")
                   and isinstance(v, list))
    log_lines = stats.get("lines", 0)
    tables = len(db.keys())
    rows = sum(len(v) for v in db.values() if isinstance(v, list))
    catalog_keys = len(codeindex.catalog())

    # A ticket with no traceable ID short-circuits before logs are searched.
    # Say so rather than reporting an unknown source — "we did not look" and
    # "we looked and found nothing" are different answers.
    skipped = bool(inv.get("needs_info"))
    log_detail = ("skipped — the ticket names no order, customer, request, "
                  "reservation or notification to search on"
                  if skipped else
                  (source.get("mode") or "unknown") + f" — {log_lines} correlated lines")

    # Stage 0: did we confirm the ticket is describing something real?
    ver = inv.get("verification") or {}
    vverdict = ver.get("verdict")
    stages = []
    if ver:
        mark = {"CONFIRMED": "ok", "CONTRADICTED": "fail", "UNVERIFIABLE": "warn"}.get(vverdict, "warn")
        stages.append(stage(0, "Verify ticket against live system",
                            ver.get("summary", ""), mark, ver.get("checked")))

    return stages + [
        stage(1, "Jira issue input",
              (entities.get("incident_id") or ["free-text ticket"])[0],
              "warn" if not id_count else "ok", id_count),
        stage(2, "Retrieve logs", log_detail, "skip" if skipped else ("ok" if log_lines else "warn"), log_lines),
        stage(3, "Analyze code",
              f"{catalog_keys} classes indexed",
              "ok" if catalog_keys else "skip", catalog_keys),
        stage(4, "AI analysis engine",
              llm.get("engine") or (llm.get("model") if llm.get("enabled") else "not configured"),
              "ok" if llm.get("enabled") else "warn"),
        stage(5, "Developer output",
              f"{tables} tables read, {rows} rows",
              "ok" if (inv.get("verdict") or {}) else "warn", rows),
    ]


@app.get("/api/health")
def health():
    """Dependency status for the console's status row. Never fails the app."""
    return {
        "status": "UP",
        "service": "bugzero",
        "sources": {
            "logs": logsearch.active_source(),
            "ssh": ssh_collector.health(),
            "llm": chains.health(),
            "jira": jira_client.health(),
            "email": mailer.health(),
            "store": store.health(),
        },
    }


@app.post("/api/investigate")
def investigate(req: InvestigateRequest):
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    from . import verifier
    verification = verifier.verify(req.incident_text)
    inv = investigator.investigate(req.incident_text, mode=mode)
    inv["verification"] = verification
    inv["stages"] = _stages(inv)
    saved = store.save_investigation(inv)
    inv["investigation_ref"] = saved.get("reference")
    inv["investigation_id"] = saved.get("id")
    inv["persisted"] = saved.get("ok", False)
    if not saved.get("ok"):
        inv.setdefault("warnings", []).append(
            f"History store unavailable ({saved.get('error')}) — evidence returned, history not saved.")
    return inv


@app.post("/api/rca")
def make_rca(req: InvestigateRequest):
    """Full pipeline + structured RCA document (JSON + markdown).

    Carries the located code fix and an explicit remediation plan. Previously the
    fix only appeared in the handoff output, so an RCA for a platform failure
    documented the problem and then stopped without saying what to do.
    """
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    from . import verifier
    verification = verifier.verify(req.incident_text)
    inv = investigator.investigate(req.incident_text, mode=mode)
    inv["verification"] = verification
    inv["stages"] = _stages(inv)
    fix = handoff._code_fix_block(inv)
    doc = rca_builder.build_rca(inv, fix)
    saved = store.save_investigation(inv, source_issue=None)
    # Read has_fix from the document, not from the draft: build_rca discards the
    # fix when verification contradicts the ticket, and the response must agree.
    return {"rca": doc, "markdown": rca_builder.to_markdown(doc),
            "investigation": inv,
            "investigation_ref": saved.get("reference"),
            "has_fix": "code_fix" in doc,
            "verification": doc.get("verification"),
            "remediation": doc.get("remediation")}


@app.get("/api/catalog")
def catalog(service: str = None):
    """Real classes and public methods per service, for grounding code fixes."""
    cat = codeindex.catalog()
    if service:
        key = next((k for k in cat if k.startswith(service)), None)
        return {"classes": {key: cat[key]} if key else {}}
    return {"classes": cat}


# --------------------------------------------------------------------------
# Jira — intake and read
# --------------------------------------------------------------------------

@app.get("/api/jira/issues")
def jira_issues(project_key: str = None, jql: str = None, max_results: int = 25):
    """Candidate tickets for the console's picker.

    Falls back to the built-in incident queue when Jira is not configured, so
    the picker is never empty in a demo or a sandbox. `source` tells the console
    which it got.
    """
    if not jira_client.is_configured():
        data = live_incidents()
        return {**data, "source": "incident-queue",
                "note": ("Jira is not configured — showing the built-in incident "
                         "queue (scenario cards plus live database findings).")}
    res = jira_client.list_issues(project_key=project_key, jql=jql, max_results=max_results)
    return {**res, "source": "jira"}


@app.get("/api/incidents/{key}")
def scenario_card(key: str):
    """One scenario card, including the text the investigator reasons over."""
    from . import scenarios
    card = scenarios.get(key)
    if not card:
        return {"ok": False, "error": f"No scenario card {key}"}
    return {"ok": True, "card": card}


@app.get("/api/jira/issue")
def jira_issue(issue_key: str):
    return jira_client.get_issue(issue_key)


@app.post("/api/jira/transition")
def jira_transition(req: dict):
    return jira_client.transition(req.get("issue_key", ""),
                                  req.get("transition", ""))


@app.post("/api/investigate/jira")
def investigate_jira(req: JiraIssueRequest):
    """Slide 3 stage 1: pull the real ticket, then investigate its text."""
    issue = jira_client.get_issue(req.issue_key)
    if not issue.get("ok"):
        # Jira unreachable: still investigate whatever we were given so the
        # operator is not blocked, but say plainly that the ticket was not read.
        inv = investigator.investigate(req.issue_key, mode=req.mode)
        inv["stages"] = _stages(inv)
        inv["jira_source"] = {"ok": False, "error": issue.get("error")}
        return inv
    parts = [
        f"Jira {issue['key']}: {issue['summary']}",
        f"Status: {issue['status']} · Priority: {issue['priority']} · "
        f"Reporter: {issue['reporter']}",
    ]
    if issue.get("description"):
        parts += ["", issue["description"]]
    for c in issue.get("comments", [])[-3:]:
        parts += ["", f"Comment by {c['author']}: {c['body']}"]
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    inv = investigator.investigate("\n".join(parts), mode=mode)
    inv["stages"] = _stages(inv)
    inv["jira_source"] = {"ok": True, "key": issue["key"], "summary": issue["summary"],
                          "status": issue["status"], "priority": issue["priority"]}
    saved = store.save_investigation(inv, source_issue=issue["key"])
    inv["investigation_ref"] = saved.get("reference")
    inv["persisted"] = saved.get("ok", False)
    return inv


# --------------------------------------------------------------------------
# Handoff — the slide 3 closing loop
# --------------------------------------------------------------------------

@app.post("/api/handoff")
def handoff_endpoint(req: HandoffRequest):
    """Package evidence into a Jira ticket + developer email.

    Returns the rendered artifacts even when a channel is unconfigured, so an
    operator can copy them manually. `dry_run` builds everything and transmits
    nothing — that is what the console's Preview button calls.
    """
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    inv = investigator.investigate(req.incident_text, mode=mode)
    inv["stages"] = _stages(inv)
    saved = store.save_investigation(inv, source_issue=req.source_issue_key)
    inv_id = saved.get("id")

    out = handoff.handoff(
        inv,
        source_issue_key=req.source_issue_key,
        send_email=req.send_email,
        create_ticket=req.create_ticket,
        to_addrs=req.to_addrs,
        extra_labels=req.extra_labels,
    )
    out["investigation_ref"] = saved.get("reference")
    hsaved = store.save_handoff(out, investigation_id=inv_id)
    out["handoff_ref"] = hsaved.get("reference")
    out["persisted"] = bool(saved.get("ok") and hsaved.get("ok"))
    out["rca_markdown"] = rca_builder.to_markdown(rca_builder.build_rca(inv))
    return out


@app.post("/api/handoff/dry-run")
def handoff_dry_run(req: HandoffRequest):
    """Render the ticket and email without transmitting anything."""
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    inv = investigator.investigate(req.incident_text, mode=mode)
    inv["stages"] = _stages(inv)
    out = handoff.handoff(inv, source_issue_key=req.source_issue_key,
                          send_email=False, create_ticket=False)
    out["dry_run"] = True
    out["note"] = ("Preview only — nothing was sent. The descriptions below are "
                   "exactly what would go to Jira and to the developer.")
    out["rca_markdown"] = rca_builder.to_markdown(rca_builder.build_rca(inv))
    return out


@app.post("/api/handoff/comment")
def handoff_comment(req: dict):
    """Post a re-investigation's evidence onto an existing Jira ticket."""
    text = req.get("incident_text", "")
    key = req.get("issue_key", "")
    inv = investigator.investigate(text, mode=req.get("mode", "llm"))
    inv["stages"] = _stages(inv)
    return {"comment": handoff.comment_on_issue(key, inv)}


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------

@app.get("/api/history")
def history(limit: int = 50, status: str = None, source_issue: str = None):
    res = store.list_investigations(limit=limit, status=status,
                                    source_issue=source_issue)
    res["stats"] = store.stats()
    return res


@app.get("/api/history/detail")
def history_detail(reference: str):
    return store.get_investigation(reference)


@app.post("/api/history/followup")
def history_followup(req: FollowupRequest):
    """Close the loop: record the developer's outcome against the investigation.

    Accepts either the numeric id or the BZI- reference the console shows.
    """
    inv_id = int(req.reference) if str(req.reference).isdigit() else None
    if inv_id is None:
        got = store.get_investigation(req.reference)
        if not got.get("ok"):
            return {"ok": False, "error": got.get("error", "unknown investigation")}
        inv_id = got["investigation"]["id"]
    return store.add_followup(inv_id, req.note, req.new_status, req.jira_key)


@app.post("/api/setup/schema")
def setup_schema():
    """Create BugZero's own tables. Only ever touches the bugzero database."""
    return store.ensure_schema()


@app.post("/api/chat")
def chat(req: ChatRequest):
    """Conversational ops: investigate, RCA, health, reproduce, help."""
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    return chatbot.chat(req.message, mode=mode)


@app.get("/api/incidents")
def live_incidents():
    """Incident queue: the scenario card library plus live database findings.

    Both sources matter. The DB-derived tickets find trouble nobody has filed
    yet. The scenario cards are the reproducible library — and they are the only
    way to see the platform-failure scenarios at all, because those leave an
    order in a normal, retryable state and so leave no trace in the database.

    `source` distinguishes them: `scenario` (a card you can run) versus `live`
    (something the databases suggest right now).
    """
    from . import dbcheck, scenarios
    cands = dbcheck.recent_candidates()
    tickets = []
    # Scenario cards first — they are the curated, runnable set.
    tickets.extend(scenarios.load())
    for o in cands.get("suspicious_orders", []):
        status = o.get("status", "")
        verb = {"FAILED": "failed", "PAYMENT_PENDING": "stuck with payment recorded but unvalidated",
                "RETRYING": "parked without revalidation"}.get(status, f"in state {status}")
        tickets.append({
            "id": f"LIVE-ORD-{o.get('order_number')}",
            "title": f"Order {o.get('order_number')} {verb}",
            "status": "Open", "priority": "P2", "reporter": "Ops watchlist",
            "source": "live", "kind": "domain", "http_status": None,
            "requires_chaos": False,
            "order": o.get("order_number"), "error": status,
            "text": f"Order {o.get('order_number')} {verb} (order {o.get('id')}).",
        })
    for p in cands.get("failed_provisioning", []):
        tickets.append({
            "id": f"LIVE-PRV-{p.get('request_number')}",
            "title": f"Provisioning {p.get('request_number')} failed",
            "status": "Open", "priority": "P2", "reporter": "Ops watchlist",
            "source": "live", "kind": "domain", "http_status": None,
            "requires_chaos": False,
            "order": "—", "error": (p.get("last_error") or "FAILED")[:60],
            "text": f"Provisioning {p.get('request_number')} ({p.get('service_type')}) FAILED.",
        })
    for n in cands.get("failed_notifications", []):
        tickets.append({
            "id": f"LIVE-NTF-{n.get('notification_number')}",
            "title": f"Notification {n.get('notification_number')} failed",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "source": "live", "kind": "domain", "http_status": None,
            "requires_chaos": False,
            "order": "—", "error": (n.get("last_error") or "FAILED")[:60],
            "text": f"Notification {n.get('notification_number')} ({n.get('channel')}) FAILED.",
        })
    for r in cands.get("open_reservations", []):
        tickets.append({
            "id": f"LIVE-RSV-{r.get('reservation_number')}",
            "title": f"Hold {r.get('reservation_number')} still open",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "source": "live", "kind": "domain", "http_status": None,
            "requires_chaos": False,
            "order": f"order {r.get('order_id')}", "error": f"resource {r.get('resource_number')} held",
            "text": (f"Reservation {r.get('reservation_number')} stuck ACTIVE for order {r.get('order_id')}; "
                     f"resource {r.get('resource_number')} blocked."),
        })
    for d in cands.get("duplicate_notifications", []):
        tickets.append({
            "id": f"LIVE-DUP-{d.get('order_id')}",
            "title": f"Duplicate {d.get('template_code')} notifications for order {d.get('order_id')}",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "source": "live", "kind": "domain", "http_status": None,
            "requires_chaos": False,
            "order": f"order {d.get('order_id')}", "error": f"{d.get('n')}× {d.get('template_code')} sent",
            "text": (f"Duplicate {d.get('template_code')} notifications ({d.get('n')} rows) "
                     f"for order {d.get('order_id')}."),
        })
    for v in cands.get("vanished_promos", []):
        tickets.append({
            "id": f"LIVE-PROMO-{v.get('order_number')}",
            "title": f"Promo vanished from order {v.get('order_number')}",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "source": "live", "kind": "domain", "http_status": None,
            "requires_chaos": False,
            "order": v.get("order_number"),
            "error": "PROMOTION_APPLIED in history, promo now NULL",
            "text": (f"Promo was applied to order {v.get('order_number')} then silently cleared "
                     f"by a modify (order {v.get('id')})."),
        })
    http_n = sum(1 for t in tickets if t.get("kind") == "http-platform")
    return {"tickets": tickets, "count": len(tickets),
            "scenario_count": len(scenarios.load()),
            "live_count": len(tickets) - len(scenarios.load()),
            "http_failure_count": http_n}


@app.post("/api/datafix")
def data_fix(req: InvestigateRequest):
    """Temporary data-fix proposals for a ticket (proposal only, approval required)."""
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    inv = investigator.investigate(req.incident_text, mode=mode)
    out = datafix.propose(inv)
    llm = inv.get("llm") or {}
    if llm.get("enabled") and (llm.get("analysis") or {}).get("data_fix_sql"):
        out["llm_sql"] = llm["analysis"]["data_fix_sql"]
    return {"incident_text": req.incident_text, "suspect": inv.get("suspect"), **out}


@app.post("/api/codefix")
def code_fix(req: InvestigateRequest):
    """Permanent code-fix proposals for a ticket (proposal only, review required)."""
    from . import llmreason
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    inv = investigator.investigate(req.incident_text, mode=mode)
    out = codefix.propose(inv, req.incident_text)
    llm = inv.get("llm") or {}
    if llm.get("enabled") and (llm.get("analysis") or {}).get("code_fix"):
        out["llm_code_fix"] = llm["analysis"]["code_fix"]
    # Validate drafts against the live code catalog; repair once via the model
    # when it invents names. Never show unverified locations as fact.
    for f in out.get("fixes", []):
        f["location"] = codeindex.locate(f.get("service", ""), f.get("area", ""))
    bad = [f for f in out.get("fixes", [])
           if f.get("location") and not (f["location"].get("methods"))
           and f.get("class_name")]
    if bad and llm.get("enabled"):
        from . import codeindex as _ci
        narrowed = {}
        for f in bad:
            for key, methods in _ci.catalog().items():
                if key.endswith(":" + (f.get("class_name") or "")):
                    narrowed[key] = methods
        repair = llmreason.reason(
            req.incident_text,
            {"broken_drafts": [{k: f.get(k) for k in ("title", "service", "area", "class_name", "method", "before", "after")} for f in bad],
             "valid_methods_only": narrowed,
             "instruction": ("Your draft used a method name that DOES NOT EXIST. "
                             "Set method to one of the valid methods listed above — character for character. "
                             "If none fits the change, set method to the closest real one and say so in area.")},
            system_override=(
                "You draft Java code fixes. Re-emit ONLY a JSON object {\"fixes\": [...]} "
                "with the same fixes, corrected: class_name AND method MUST be copied "
                "EXACTLY from valid_methods_only. before/after MUST be Java code blocks "
                "(3+ lines) calling those real methods in Spring/JPA style. "
                "code_fix shape: {service, area, class_name, method, before, after}."))
        fixed = ((repair.get("analysis") or {}).get("fixes")
                 if isinstance(repair.get("analysis"), dict) else None) or repair.get("fixes")
        if isinstance(fixed, list) and fixed:
            bad_ids = {id(f) for f in bad}
            queue = [g for g in fixed if isinstance(g, dict)]
            for f in out["fixes"]:
                if id(f) in bad_ids and queue:
                    g = queue.pop(0)
                    for k in ("class_name", "method", "before", "after", "area", "title"):
                        if g.get(k):
                            f[k] = g[k]
                    f["location"] = codeindex.locate(f.get("service", ""),
                                                     f" {f.get('class_name','')} {f.get('method','')}")
                    f["repaired"] = True
    return {"incident_text": req.incident_text, "suspect": inv.get("suspect"), **out}
