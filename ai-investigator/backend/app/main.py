"""AI Production Incident Investigator API (Phase 14).

Run:  uvicorn app.main:app --port 8090   (from ai-investigator/backend)
Docs: http://localhost:8090/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import chatbot, investigator, rca as rca_builder

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
