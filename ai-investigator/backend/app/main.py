"""AI Production Incident Investigator API (Phase 14).

Run:  uvicorn app.main:app --port 8090   (from ai-investigator/backend)
Docs: http://localhost:8090/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import chatbot, codefix, codeindex, datafix, investigator, rca as rca_builder

app = FastAPI(title="AI Incident Investigator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Both localhost spellings: browsers treat 127.0.0.1 and localhost as different origins.
    allow_origins=["http://localhost:5173", "http://localhost:4200",
                   "http://127.0.0.1:5173", "http://127.0.0.1:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvestigateRequest(BaseModel):
    incident_text: str
    mode: str = "llm"  # "llm" (default, falls back gracefully) or "rules"


class ChatRequest(BaseModel):
    message: str
    mode: str = "llm"


@app.get("/api/health")
def health():
    return {"status": "UP", "service": "ai-investigator"}


@app.post("/api/investigate")
def investigate(req: InvestigateRequest):
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    return investigator.investigate(req.incident_text, mode=mode)


@app.post("/api/rca")
def make_rca(req: InvestigateRequest):
    """Full pipeline + structured RCA document (JSON + markdown)."""
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    inv = investigator.investigate(req.incident_text, mode=mode)
    doc = rca_builder.build_rca(inv)
    return {"rca": doc, "markdown": rca_builder.to_markdown(doc),
            "investigation": inv}


@app.post("/api/chat")
def chat(req: ChatRequest):
    """Conversational ops: investigate, RCA, health, reproduce, help."""
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    return chatbot.chat(req.message, mode=mode)


@app.get("/api/incidents")
def live_incidents():
    """Live incident queue built from current DB state (no hardcoded tickets).
    Each item carries ready-to-investigate ticket text with real IDs."""
    from . import dbcheck
    cands = dbcheck.recent_candidates()
    tickets = []
    for o in cands.get("suspicious_orders", []):
        status = o.get("status", "")
        verb = {"FAILED": "failed", "PAYMENT_PENDING": "stuck with payment recorded but unvalidated",
                "RETRYING": "parked without revalidation"}.get(status, f"in state {status}")
        tickets.append({
            "id": f"LIVE-{o.get('order_number')}",
            "title": f"Order {o.get('order_number')} {verb}",
            "status": "Open", "priority": "P2", "reporter": "Ops watchlist",
            "order": o.get("order_number"), "error": status,
            "text": f"Order {o.get('order_number')} {verb} (order {o.get('id')}).",
        })
    for p in cands.get("failed_provisioning", []):
        tickets.append({
            "id": f"LIVE-{p.get('request_number')}",
            "title": f"Provisioning {p.get('request_number')} failed",
            "status": "Open", "priority": "P2", "reporter": "Ops watchlist",
            "order": "—", "error": (p.get("last_error") or "FAILED")[:60],
            "text": f"Provisioning {p.get('request_number')} ({p.get('service_type')}) FAILED.",
        })
    for n in cands.get("failed_notifications", []):
        tickets.append({
            "id": f"LIVE-{n.get('notification_number')}",
            "title": f"Notification {n.get('notification_number')} failed",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "order": "—", "error": (n.get("last_error") or "FAILED")[:60],
            "text": f"Notification {n.get('notification_number')} ({n.get('channel')}) FAILED.",
        })
    for r in cands.get("open_reservations", []):
        tickets.append({
            "id": f"LIVE-{r.get('reservation_number')}",
            "title": f"Hold {r.get('reservation_number')} still open",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "order": f"order {r.get('order_id')}", "error": f"resource {r.get('resource_number')} held",
            "text": (f"Reservation {r.get('reservation_number')} stuck ACTIVE for order {r.get('order_id')}; "
                     f"resource {r.get('resource_number')} blocked."),
        })
    for d in cands.get("duplicate_notifications", []):
        tickets.append({
            "id": f"LIVE-DUP-{d.get('order_id')}",
            "title": f"Duplicate {d.get('template_code')} notifications for order {d.get('order_id')}",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "order": f"order {d.get('order_id')}", "error": f"{d.get('n')}× {d.get('template_code')} sent",
            "text": (f"Duplicate {d.get('template_code')} notifications ({d.get('n')} rows) "
                     f"for order {d.get('order_id')}."),
        })
    for v in cands.get("vanished_promos", []):
        tickets.append({
            "id": f"LIVE-{v.get('order_number')}",
            "title": f"Promo vanished from order {v.get('order_number')}",
            "status": "Open", "priority": "P3", "reporter": "Ops watchlist",
            "order": v.get("order_number"), "error": "PROMOTION_APPLIED in history, promo now NULL",
            "text": (f"Promo was applied to order {v.get('order_number')} then silently cleared "
                     f"by a modify (order {v.get('id')})."),
        })
    return {"tickets": tickets, "count": len(tickets)}


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
