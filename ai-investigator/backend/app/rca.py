"""Structured Root Cause Analysis documents (Phase 15).

build_rca() turns an investigate() result into the formal RCA shape:
INCIDENT, IMPACT, AFFECTED, TIMELINE, EVIDENCE, ROOT CAUSE, CONTRIBUTING
FACTORS, CONFIDENCE, RECOMMENDATION — plus markdown rendering for download.
Rule-built sections are deterministic; when the LLM is enabled its analysis
is embedded as the narrative (clearly labeled).
"""

from datetime import datetime, timezone


def _first(d: dict, key: str):
    rows = (d.get("db_evidence") or {}).get(key, [])
    return rows[0] if rows else {}


def _impact(inv: dict) -> str:
    orders = (inv.get("db_evidence") or {}).get("orders", [])
    if not orders:
        return "Impact follows from the verdict once the LLM has read the evidence."
    o = orders[0]
    return (f"Order {o.get('order_number')} sits in {o.get('status')} — "
            f"see verdict for customer impact.")


def _contributing(inv: dict) -> list:
    analysis = ((inv.get("llm") or {}).get("analysis") or {}) if (inv.get("llm") or {}).get("enabled") else {}
    refs = analysis.get("evidence_refs", []) if analysis else []
    if refs:
        return [f"Evidence link: {r}" for r in refs]
    return ["LLM key required — contributing factors are drafted by the model from evidence."]


def _recommendation(inv: dict) -> str:
    analysis = ((inv.get("llm") or {}).get("analysis") or {}) if (inv.get("llm") or {}).get("enabled") else {}
    steps = (analysis.get("next_steps") or []) if analysis else []
    base = ("The Temporary workaround and Permanent code fix sections below load "
            "automatically with this RCA — review the workaround first, then the code fix. ")
    if steps:
        base += "Suggested order: " + "; ".join(steps[:4]) + "."
    else:
        base += "Enable the LLM for tailored next steps."
    return base


def build_rca(inv: dict) -> dict:
    """Assemble the RCA document from an investigate() result."""
    entities = inv.get("entities", {})
    db = inv.get("db_evidence", {})
    orders = db.get("orders", [])
    order = orders[0] if orders else {}
    journey = inv.get("journey", [])
    times = [j["time"] for j in journey if j.get("time")]
    llm = inv.get("llm") or {}
    analysis = llm.get("analysis") if llm.get("enabled") else None

    rca = {
        "incident": (entities.get("incident_id") or ["UNNUMBERED"])[0],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": inv.get("mode", "llm"),
        "summary": (analysis.get("summary") if analysis
                    else (inv.get("verdict") or {}).get("summary")
                    or "Evidence collected below; add the LLM key for the verdict narrative."),
        "impact": _impact(inv),
        "affected": {
            "order": order.get("order_number"),
            "order_status": order.get("status"),
            "customer": entities.get("customer_number", entities.get("customer_id", [])),
            "service": ", ".join((inv.get("owner") or {}).get("services", [])) or "see evidence",
        },
        "timeline": [
            {"time": j.get("time"), "service": j.get("service"),
             "event": j.get("event"), "status": j.get("status")}
            for j in journey
        ],
        "window": {"first": min(times) if times else None, "last": max(times) if times else None},
        "evidence": {
            "correlations": inv.get("correlations", []),
            "log_lines": len(inv.get("log_lines", [])),
            "db_tables": sorted(db.keys()),
        },
        "root_cause": (analysis.get("root_cause") if analysis
                       else "LLM key required — the model drafts the root cause from the evidence above."),
        "contributing_factors": _contributing(inv),
        "confidence": (analysis.get("confidence") if analysis
                       else verdict.get("confidence", "none")),
        "fix_type": (analysis.get("fix_type") if analysis
                     else verdict.get("fix_type", "UNKNOWN")),
        "recommendation": _recommendation(inv),
    }
    if analysis:
        rca["llm_narrative"] = {
            "evidence_refs": analysis.get("evidence_refs", []),
            "next_steps": analysis.get("next_steps", []),
        }
    return rca


def to_markdown(rca: dict) -> str:
    """Render the RCA as downloadable markdown."""
    a = rca["affected"]
    lines = [
        f"# RCA — {rca['incident']}",
        f"_Generated {rca['generated_at']} · mode {rca['mode']} · confidence {rca['confidence']}_",
        "",
        "## Incident",
        rca["summary"],
        "",
        "## Impact",
        rca["impact"],
        "",
        "## Affected",
        f"- Order: {a.get('order')} (status {a.get('order_status')})",
        f"- Customer: {a.get('customer')}",
        f"- Service: {a.get('service')}",
        "",
        "## Timeline",
    ]
    for t in rca["timeline"]:
        lines.append(f"- `{t['time']}` {t['service']} — {t['event']} ({t['status']})")
    lines += ["", "## Evidence",
              f"- correlations: {', '.join(rca['evidence']['correlations']) or 'none'}",
              f"- correlated log lines: {rca['evidence']['log_lines']}",
              f"- db tables read: {', '.join(rca['evidence']['db_tables']) or 'none'}",
              "", "## Root cause", rca["root_cause"], "", "## Contributing factors"]
    lines += [f"- {c}" for c in rca["contributing_factors"]]
    lines += ["", "## Recommendation", rca["recommendation"], ""]
    if "llm_narrative" in rca:
        lines += ["## LLM evidence chain"]
        lines += [f"- {e}" for e in rca["llm_narrative"]["evidence_refs"]]
        lines += ["", "## LLM next steps"]
        lines += [f"- {s}" for s in rca["llm_narrative"]["next_steps"]]
        lines += [""]
    return "\n".join(lines)
