"""AI Production Incident Investigator API (Phase 14).

Run:  uvicorn app.main:app --port 8090   (from ai-investigator/backend)
Docs: http://localhost:8090/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import chatbot, codefix, datafix, investigator, rca as rca_builder

app = FastAPI(title="AI Incident Investigator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4200"],
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
    mode = req.mode if req.mode in ("llm", "rules") else "llm"
    inv = investigator.investigate(req.incident_text, mode=mode)
    out = codefix.propose(inv, req.incident_text)
    llm = inv.get("llm") or {}
    if llm.get("enabled") and (llm.get("analysis") or {}).get("code_fix"):
        out["llm_code_fix"] = llm["analysis"]["code_fix"]
    return {"incident_text": req.incident_text, "suspect": inv.get("suspect"), **out}
