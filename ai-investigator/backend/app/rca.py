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
    status = orders[0].get("status") if orders else None
    svc = (inv.get("suspect") or {}).get("service") or "unidentified service"
    mapping = {
        "PAYMENT_PENDING": "customer charged (or attempting) but service not provisioned; order frozen mid-funnel",
        "FAILED": "order/request dead; customer without service until re-driven",
        "RETRYING": "order parked; invisible to queues except status filters",
        "RESERVED": "sellable stock reduced; future orders may starve",
    }
    impact = mapping.get(str(status), "degraded order journey; customer-facing delay")
    return f"{impact} (suspect: {svc})"


def _contributing(inv: dict) -> list:
    out = []
    journey = inv.get("journey", [])
    events = [j["event"] for j in journey]
    if "PAYMENT_RECORDED" in events and "PAYMENT_VALIDATED" not in events:
        out.append("No callback/scheduler guarantees the validation step runs after a recorded payment.")
    if "RESOURCE_RESERVED" in events and "RESOURCE_ALLOCATED" not in events:
        out.append("No expiry sweeper or TTL enforcement frees stale reservations.")
    if sum(1 for e in events if e == "NOTIFICATION_QUEUED") > 1:
        out.append("Notify path has no idempotency key on (order, event); retries duplicate.")
    if "MISSING_MSISDN" in str(inv.get("db_evidence", {})) or "MISSING_RESOURCE" in str(inv.get("db_evidence", {})):
        out.append("Upstream flow allows provisioning requests without required identifiers.")
    if not out:
        out.append("Single-point failure with no contributing automation gaps identified.")
    return out


def _recommendation(inv: dict) -> str:
    hint = inv.get("fix_hint", "")
    if hint.startswith("DATA"):
        return ("First, unblock the affected rows with the temporary data correction under "
                "'Propose fixes' below — read the SQL, check it, run it yourself; nothing here "
                "runs automatically. Then fix the step or check that created the bad state, "
                "so it cannot happen again.")
    if hint.startswith("CODE"):
        return ("A data patch only buys time for this one. If customers are waiting, use the "
                "temporary workaround under 'Propose fixes' to unblock them, then schedule the "
                "permanent code change described there and cover it with the listed tests.")
    return ("First establish whether this is a data or code problem — use 'Propose fixes' "
            "below and compare both proposals against the evidence above.")


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
        "mode": inv.get("mode", "rules"),
        "summary": (analysis or {}).get("summary") or inv.get("hypothesis", ""),
        "impact": _impact(inv),
        "affected": {
            "order": order.get("order_number"),
            "order_status": order.get("status"),
            "customer": entities.get("customer_number", entities.get("customer_id", [])),
            "service": (inv.get("suspect") or {}).get("service"),
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
        "root_cause": (analysis or {}).get("root_cause")
        or f"{(inv.get('suspect') or {}).get('signal')} in {(inv.get('suspect') or {}).get('service')}",
        "contributing_factors": _contributing(inv),
        "confidence": (analysis or {}).get("confidence")
        or (inv.get("suspect") or {}).get("confidence"),
        "fix_type": (analysis or {}).get("fix_type") or inv.get("fix_hint", "UNKNOWN"),
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
