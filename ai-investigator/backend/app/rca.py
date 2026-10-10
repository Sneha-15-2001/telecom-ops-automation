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
    """What the customer or the business actually felt.

    Uses the model's reading of the evidence when there is one. The previous
    version always returned a placeholder sentence telling you to read the
    verdict instead — which made every RCA's impact section content-free.
    """
    analysis = ((inv.get("llm") or {}).get("analysis") or {}) if (inv.get("llm") or {}).get("enabled") else {}
    if analysis.get("impact"):
        return analysis["impact"]
    orders = (inv.get("db_evidence") or {}).get("orders", [])
    if orders:
        o = orders[0]
        return (f"Order {o.get('order_number')} is stuck in {o.get('status')}. "
                f"See the verdict for who is affected.")
    hint = (inv.get("message") or "").strip()
    # `message` is an instruction to the operator when the ticket had no
    # traceable ID ("add an order number..."). That is not an impact statement
    # and must never reach a customer-facing section.
    if inv.get("needs_info"):
        return ("No specific customer record was affected — this ticket describes a "
                "platform fault rather than one order. See 'What has to be done' below.")
    return hint or ("No customer record was affected — this is a platform fault. "
                    "See the remediation section for what to do.")


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


# Not a defect: the system correctly refused work it cannot do. These still need
# an answer — "here is what you must build before this works" — not silence.
NOT_DEFECT_CODES = {
    "NOT_IMPLEMENTED": "capability-not-built",
    "RATE_LIMIT_EXCEEDED": "client-backoff",
    "TOO_MANY_REQUESTS": "client-backoff",
    "UNSUPPORTED_MEDIA_TYPE": "malformed-request",
    "METHOD_NOT_ALLOWED": "malformed-request",
    "INVALID_PARAMETER": "malformed-request",
    "VALIDATION_ERROR": "malformed-request",
    "INVALID_REQUEST": "malformed-request",
    "MISSING_PARAMETER": "malformed-request",
}


def _remediation(inv: dict, code_fix: dict = None) -> dict:
    """What actually has to be done, ordered, with an owner.

    This is the section people act on. It has three cases, because there are
    three genuinely different situations:

      * a code defect          -> fix the named method, then deploy
      * not a defect (501/429) -> integrate or back off; nothing is broken
      * no fix identified      -> say so, and give the checks to run instead

    Returning nothing here is what made platform-failure tickets look empty: a
    501 has no code to change, and the previous build reported that as silence
    rather than as a build plan.
    """
    llm = inv.get("llm") or {}
    analysis = (llm.get("analysis") or {}) if llm.get("enabled") else {}
    steps = list(analysis.get("next_steps") or [])
    confidence = analysis.get("confidence") or (inv.get("verdict") or {}).get("confidence") or "low"
    entities = inv.get("entities") or {}
    hints = entities.get("error_hints") or []

    # Known non-defects are decided BEFORE looking at any proposed fix. The model
    # will happily draft a change for a 429 — it proposed editing
    # OrderController.create() — but a throttled client is not a code defect and
    # sending that as a "fix" would waste a developer's afternoon.
    for hint in hints:
        kind = NOT_DEFECT_CODES.get(hint.upper())
        if kind == "capability-not-built":
            return {
                "kind": "not-a-defect", "owner": "platform team", "where": None,
                "steps": [
                    "Nothing is broken — the service correctly refused a capability "
                    "this deployment does not have (501).",
                    "Read the 501 message: it names the exact integration required.",
                    "Build that integration (payment gateway, or mediation + HLR/HSS/OSS "
                    "adapter) before this endpoint can serve real traffic.",
                    "Until then, do not route live traffic to it — a 200 with a fake "
                    "success would be worse than the 501.",
                ],
                "confidence": "high",
                "note": "Roadmap item, not a defect. Any code change the model proposed "
                        "for this ticket was discarded.",
            }
        if kind == "client-backoff":
            return {
                "kind": "not-a-defect", "owner": "calling integration", "where": None,
                "steps": [
                    "The server is behaving correctly — the client exceeded its budget.",
                    "Honour the Retry-After header and back off for that many seconds.",
                    "Stop retrying on 429; each retry spends more of the same budget.",
                    "Agents behind one NAT share a budget, so a busy branch starves "
                    "itself while another is fine — that asymmetry is expected.",
                    "If the limit is genuinely too low, raise RATE_LIMIT_MAX_REQUESTS "
                    "rather than disabling the limiter.",
                ],
                "confidence": "high",
                "note": "Client-side fix. No server change is warranted.",
            }
        if kind == "malformed-request":
            return {
                "kind": "not-a-defect", "owner": "calling integration", "where": None,
                "steps": [
                    "The server is behaving correctly — the request itself was malformed.",
                    "415 means the Content-Type is wrong: send application/json.",
                    "405 means the HTTP verb is wrong for that path; the response lists "
                    "the verbs that are allowed.",
                    "400 INVALID_PARAMETER usually means an enum or numeric value out of "
                    "range; the message names the acceptable values.",
                    "No server change is required. Re-test from the corrected client.",
                ],
                "confidence": "high",
                "note": "Client-side fix. No server change is warranted.",
            }

    # A located, verified method is the strongest signal we have.
    if code_fix and (code_fix.get("location") or {}).get("methods"):
        loc = code_fix["location"]["methods"][0]
        where = f"{loc.get('file')}:{loc.get('start_line')}-{loc.get('end_line')}"
        steps.insert(0,
            f"Fix {code_fix.get('class_name')}.{code_fix.get('method')}() in {where} — "
            f"that is where the '{code_fix.get('service')}' request is mishandled.")
        return {"kind": "code-defect", "owner": code_fix.get("service"),
                "where": where, "steps": steps[:8],
                "confidence": confidence,
                "note": "PROPOSAL ONLY — a human reviews, tests and deploys."}

    # No fix identified. Give checks to run rather than inventing a change.
    if not steps:
        steps = [
            "No fix was identified from the evidence collected.",
            "Confirm the correlation ID in the logs and follow the stack trace.",
            "Check each dependency's /actuator/health during the failure window.",
            "Re-run the investigation once the trace is in hand.",
        ]
    return {"kind": "investigate-further", "owner": "on-call", "where": None,
            "steps": steps[:8], "confidence": confidence,
            "note": "Insufficient evidence to propose a code change."}


def build_rca(inv: dict, code_fix: dict = None) -> dict:
    """Assemble the RCA document from an investigate() result."""
    entities = inv.get("entities", {})
    db = inv.get("db_evidence", {})
    orders = db.get("orders", [])
    order = orders[0] if orders else {}
    journey = inv.get("journey", [])
    times = [j["time"] for j in journey if j.get("time")]
    llm = inv.get("llm") or {}
    analysis = llm.get("analysis") if llm.get("enabled") else None
    verdict = inv.get("verdict") or {}

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
        "remediation": _remediation(inv, code_fix),
    }
    if code_fix:
        rca["code_fix"] = {
            "service": code_fix.get("service"),
            "class_name": code_fix.get("class_name"),
            "method": code_fix.get("method"),
            "location": code_fix.get("location"),
            "verified": code_fix.get("verified"),
            "before": code_fix.get("before"),
            "after": code_fix.get("after"),
        }

    # Verification is applied AFTER the fix is attached, so it can withdraw it.
    # Proposing an edit to a system that is demonstrably healthy is the worst
    # outcome this tool can produce.
    ver = inv.get("verification") or {}
    if ver.get("checked"):
        rca["verification"] = ver
        if ver.get("verdict") == "CONTRADICTED":
            rca.pop("code_fix", None)
            rca["recommendation"] = (
                "Do not change code on the strength of this ticket. The running system "
                "disagrees with it — re-confirm the outage before investigating further.")
            rca["remediation"] = {
                "kind": "stale-ticket", "owner": "reporter",
                "where": None,
                "steps": [
                    "The ticket describes an outage that is not happening right now.",
                    "Confirm with the reporter whether the incident is already resolved "
                    "or was never real.",
                    "If it was real, check whether the logs have rotated past the evidence.",
                    "Re-run once the fault is present so the investigation has something "
                    "to read.",
                ],
                "confidence": "high",
                "note": "No code change proposed — the system is behaving.",
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

    # The action plan and the proposed change are the parts anyone actually
    # works from, so they are not buried under the LLM appendix.
    ver = rca.get("verification") or {}
    if ver.get("checked"):
        lines += ["## Ticket verified against the live system", "",
                  f"**Result: {ver.get('verdict')}** — {ver.get('summary')}", "",
                  "| Claim | Verdict | Observed |", "|---|---|---|"]
        for c in ver.get("claims", []):
            lines.append(f"| {c.get('claim')} | **{c.get('verdict')}** | {c.get('detail')} |")
        lines.append("")

    rem = rca.get("remediation") or {}
    if rem:
        lines += ["## What has to be done", ""]
        head = f"- **Type:** {rem.get('kind')}"
        if rem.get("owner"):
            head += " \u00b7 **Owner:** " + str(rem["owner"])
        lines.append(head)
        if rem.get("where"):
            lines.append("- **Where:** `" + rem["where"] + "`")
        lines += ["", "Steps, in order:"]
        for i, step in enumerate(rem.get("steps", []), 1):
            lines.append(f"{i}. {step}")
        if rem.get("note"):
            lines += ["", "_" + rem["note"] + "_"]
        lines.append("")

    fix = rca.get("code_fix") or {}
    if fix:
        mark = "verified in source" if fix.get("verified") else "NOT verified - check before acting"
        lines += ["## Proposed change", "",
                  f"**Service:** {fix.get('service')} \u00b7 **Class:** {fix.get('class_name')}"
                  f" \u00b7 **Method:** {fix.get('method')} ({mark})", ""]
        methods = (fix.get("location") or {}).get("methods") or []
        if methods:
            m = methods[0]
            lines += [f"Location: `{m.get('file')}:{m.get('start_line')}-{m.get('end_line')}`", ""]
        lines += ["**Current code (verbatim from source)**", "```java",
                  (fix.get("before") or "-")[:3000], "```", "",
                  "**Suggested replacement**", "```java",
                  (fix.get("after") or "-")[:3000], "```", ""]

    if "llm_narrative" in rca:
        lines += ["## LLM evidence chain"]
        lines += [f"- {e}" for e in rca["llm_narrative"]["evidence_refs"]]
        lines += ["", "## LLM next steps"]
        lines += [f"- {s}" for s in rca["llm_narrative"]["next_steps"]]
        lines += [""]
    return "\n".join(lines)
