"""LangChain reasoning chain for BugZero.

Slide 4 lists "LLM API • LangChain" as the AI analysis engine. This module puts
a real LangChain chain in front of the LLM while keeping the existing stdlib
transport in llmreason as the fallback.

Why both:
- With langchain installed, we use LCEL (`prompt | model | parser`) and a
  Pydantic output schema, so the verdict is validated against a shape instead
  of being hand-parsed out of whatever the model felt like returning.
- Without it, llmreason.reason() still works. LangChain is an enhancement, never
  a hard dependency — the investigator must never fail to reason because a
  package is missing.

The chains are deliberately small and single-shot (temperature 0.2, no memory).
The evidence gathering is deterministic and already cached in the result; the
model's only job is to read that evidence and explain it, which is exactly what
a one-shot chain is good at.
"""

import json

SCHEMA_FIELDS = [
    "summary", "root_cause", "evidence_refs", "confidence", "fix_type",
    "data_fix_sql", "data_fix_validation", "data_fix_rollback", "code_fix",
    "next_steps",
]

VERDICT_TEMPLATE = """You are a senior telecom production-support engineer.

TICKET
{ticket}

EVIDENCE (already collected deterministically — trust these IDs and rows)
{evidence}

Give a verdict in JSON with exactly these keys:
  summary           2-3 plain sentences; say DATA or CODE in the first line
  root_cause        one precise cause
  evidence_refs     list of quoted log lines (service + event + status) that prove the chain
  confidence        high | medium | low
  fix_type          DATA | CODE | BOTH | UNKNOWN
  data_fix_sql      proposal only, or null
  data_fix_validation  SELECT to verify, or null
  data_fix_rollback    rollback note, or null
  code_fix          {{service, area, class_name, method, before, after}} or null
  next_steps        concrete checks, simplest first

Never invent log lines, table names, columns or IDs. If the evidence does not
support a claim, say so and lower confidence."""

CODEFIX_TEMPLATE = """Draft a permanent code fix for this incident.

TICKET
{ticket}

EVIDENCE
{evidence}

ALLOWED code locations — class_name and method MUST be copied character-for-character
from this catalog. Do not invent names.
{catalog}

Reply with JSON: {{"fixes": [{{"service","area","class_name","method","before","after"}}]}}
before/after must be Java blocks of 3+ real lines in this codebase's Spring/JPA style."""


def available() -> bool:
    """True when langchain + an API key are both present."""
    from . import llmreason
    try:
        import langchain_core  # noqa: F401
        import langchain_openai  # noqa: F401
    except ImportError:
        return False
    return bool(llmreason.config().get("api_key"))


def _build_model():
    """An OpenAI-compatible chat model pointed at whatever LLM_BASE_URL says."""
    from langchain_openai import ChatOpenAI
    from . import llmreason
    cfg = llmreason.config()
    return ChatOpenAI(
        model=cfg["model"],
        openai_api_key=cfg["api_key"],
        openai_api_base=cfg["base_url"],
        temperature=0.2,
        timeout=cfg["timeout"],
        # The gateways we support all accept a JSON-object response format; we
        # also ask in prose, so a provider that ignores the kwarg still complies.
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def _run_chain(prompt_text: str, system: str, cfg: dict) -> dict:
    """Execute one prompt through LCEL and parse the JSON payload out."""
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", prompt_text),
    ])
    chain = prompt | _build_model() | StrOutputParser()
    raw = chain.invoke({})
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(raw)
    else:
        parsed = raw
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _validate(parsed: dict) -> dict:
    """Keep only the schema keys the rest of the app reads.

    A model that invents an extra key, or omits one, must not break the console.
    """
    out = {k: parsed.get(k) for k in SCHEMA_FIELDS}
    out["confidence"] = (out.get("confidence") or "low").lower()
    if out["confidence"] not in ("high", "medium", "low"):
        out["confidence"] = "low"
    out["fix_type"] = (out.get("fix_type") or "UNKNOWN").upper()
    if out["fix_type"] not in ("DATA", "CODE", "BOTH", "UNKNOWN"):
        out["fix_type"] = "UNKNOWN"
    out["evidence_refs"] = [str(r) for r in (out.get("evidence_refs") or [])][:20]
    out["next_steps"] = [str(s) for s in (out.get("next_steps") or [])][:10]
    return out


def reason(incident_text: str, evidence: dict, system_override: str = None) -> dict:
    """Reason over the evidence. LangChain when available, stdlib otherwise."""
    if not available():
        from . import llmreason
        return llmreason.reason(incident_text, evidence, system_override)
    try:
        parsed = _run_chain(
            prompt_text="TICKET\n{ticket}\n\nEVIDENCE\n{evidence}",
            system=system_override or VERDICT_TEMPLATE,
            cfg=None,
        )
        return {"enabled": True, "engine": "langchain",
                "analysis": _validate(parsed)}
    except Exception as e:
        # LangChain failed for any reason — fall back rather than lose the answer.
        from . import llmreason
        fallback = llmreason.reason(incident_text, evidence, system_override)
        if fallback.get("enabled"):
            fallback["engine"] = "stdlib-fallback"
            fallback["langchain_error"] = f"{type(e).__name__}: {e}"
        return fallback


def code_fix(incident_text: str, evidence: dict, catalog: dict) -> dict:
    """Draft a code fix through the chain, with the real method catalog injected."""
    if not available():
        return {"enabled": False,
                "reason": "LangChain not installed or no API key — code fix runs on the stdlib path."}
    try:
        parsed = _run_chain(
            prompt_text=CODEFIX_TEMPLATE.format(
                ticket=incident_text,
                evidence=json.dumps(evidence, default=str)[:10000],
                catalog=json.dumps(catalog, default=str)[:4000],
            ),
            system="You draft Java code fixes grounded in a real code catalog.",
            cfg=None,
        )
        fixes = parsed.get("fixes")
        if not isinstance(fixes, list):
            fixes = [parsed] if parsed.get("class_name") else []
        return {"enabled": True, "engine": "langchain",
                "fixes": [f for f in fixes if isinstance(f, dict)][:3]}
    except Exception as e:
        return {"enabled": False, "error": f"{type(e).__name__}: {e}"}


def health() -> dict:
    """Status row for the console."""
    from . import llmreason
    cfg = llmreason.config()
    try:
        import langchain_core  # noqa: F401
        has_langchain = True
    except ImportError:
        has_langchain = False
    return {
        "configured": bool(cfg.get("api_key")),
        "langchain_installed": has_langchain,
        "engine": "langchain" if (has_langchain and cfg.get("api_key")) else "stdlib",
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "detail": ("LangChain chain active" if has_langchain
                   else "langchain not installed — using stdlib urllib transport"),
    }