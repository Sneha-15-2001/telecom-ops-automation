"""Developer handoff — the loop-closing step (BugZero slide 3).

Takes a finished investigation and packages it for a human:

    evidence -> permanent-fix Jira ticket -> link back to the source incident
              -> developer email carrying the same evidence pack

The ticket and the mail are built from the SAME evidence bundle, so the
developer never has to re-derive why BugZero flagged this. The suggested code
location is resolved against the real source tree by codeindex first, so the
ticket points at real file:line rather than a plausible-sounding guess.

Ordering is deliberate: Jira is created first so the email can cite the ticket
key. If Jira is unconfigured the handoff still produces the full rendered
description and email body, so an operator can copy them into Jira by hand —
degraded, never silent.
"""

from . import codefix, jira_client, mailer, rca as rca_builder

# Jira rejects labels with spaces. Keep them slug-shaped.
PRIORITY_MAP = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}

# Ticket labels that make a BugZero ticket findable in a crowded board.
BASE_LABELS = ["bugzero", "ai-investigator"]


def _confidence_priority(confidence: str) -> str:
    """Low-confidence findings must not page anyone as urgent."""
    return PRIORITY_MAP.get((confidence or "low").strip().lower(), "Medium")


def _evidence_pack(inv: dict) -> dict:
    """Collapse an investigation into the handful of facts a developer needs.

    Deliberately small: a ticket that pastes 300 log lines gets ignored. The
    quoted proof lines stay, because they are the reason the ticket is credible.
    """
    llm = inv.get("llm") or {}
    analysis = (llm.get("analysis") or {}) if llm.get("enabled") else {}
    verdict = inv.get("verdict") or {}
    db = inv.get("db_evidence") or {}
    entities = inv.get("entities") or {}

    refs = analysis.get("evidence_refs") or []
    # Fall back to real correlated lines when the model gave no explicit chain,
    # so a rule-mode run still carries proof.
    if not refs:
        refs = [f"[{h['service']}] {h['line']}" for h in (inv.get("log_lines") or [])[:5]]

    order = (db.get("orders") or [{}])[0]

    def _one(values, fallback=None):
        """Entities arrive as lists; a ticket wants a single readable value."""
        if isinstance(values, list):
            values = [v for v in values if v]
            return values[0] if values else fallback
        return values or fallback

    return {
        "summary": analysis.get("summary") or verdict.get("summary") or "",
        "root_cause": analysis.get("root_cause") or verdict.get("root_cause") or "",
        "confidence": analysis.get("confidence") or verdict.get("confidence") or "low",
        "fix_type": analysis.get("fix_type") or verdict.get("fix_type") or "UNKNOWN",
        "evidence_refs": refs[:12],
        "next_steps": analysis.get("next_steps") or [],
        "correlations": inv.get("correlations") or [],
        "journey": (inv.get("journey") or [])[:40],
        "affected": {
            "incident": _one(entities.get("incident_id"), "UNNUMBERED"),
            "order": _one(entities.get("order_number")) or order.get("order_number"),
            "order_status": order.get("status"),
            "customer": _one(entities.get("customer_number")) or _one(entities.get("customer_id")),
            "services": (inv.get("owner") or {}).get("services") or [],
        },
        "log_stats": inv.get("log_stats") or {},
        "db_tables": sorted(db.keys()),
    }


def _code_fix_block(inv: dict) -> dict:
    """Proposed code change plus its verified source location.

    The "before" shown to a developer is taken from the source tree, not from
    the model. gpt-4o-mini paraphrases the code it was shown — it returns
    "// existing logic" where the real body is twenty lines — and a reviewer
    cannot diff that. The model is only asked for the "after"; we supply the
    "before" because we can read it exactly.
    """
    from . import codeindex
    proposals = codefix.propose(inv).get("fixes", [])
    if not proposals:
        return {}
    f = proposals[0]
    location = codeindex.locate(f.get("service", ""),
                                f" {f.get('class_name','')} {f.get('method','')}")
    real_source = None
    for m in (location.get("methods") or []):
        if m.get("source"):
            real_source = m["source"]
            break
    return {
        "title": f.get("title"),
        "service": f.get("service"),
        "class_name": f.get("class_name"),
        "method": f.get("method"),
        "area": f.get("area"),
        # Real source when we have it; the model's text only as a last resort.
        "before": real_source or f.get("before"),
        "after": f.get("after"),
        "before_from_source": real_source is not None,
        "location": location,
        "verified": bool(((location or {}).get("class_file") or {}).get("file")
                         and (location or {}).get("methods")),
    }


def _location_text(location: dict) -> str:
    """Render file:line references from codeindex, or say plainly it is unverified.

    codeindex returns {"class_file": {file, lines}, "methods": [{file,
    start_line, end_line, source}]}. We surface the exact span, because a
    developer opening the ticket needs to land on the method, not the class.
    """
    if not location:
        return "Source location not resolved (product repo not checked out at PRODUCT_REPO_ROOT)."
    cls = location.get("class_file") or {}
    methods = location.get("methods") or []
    if not methods:
        where = cls.get("file", "unknown file")
        span = f" lines {cls['lines']}-{cls['lines']}" if cls.get("lines") else ""
        return f"{where}{span} — method not matched in source."
    out = []
    for m in methods[:3]:
        f = m.get("file", cls.get("file", "unknown file"))
        start, end = m.get("start_line"), m.get("end_line")
        span = f":{start}" + (f"-{end}" if end and end != start else "")
        out.append(f"{f}{span}  ->  {m.get('name', '?')}()")
    return "\n".join(out)


def _is_placeholder(code: str) -> bool:
    """True when the model's "after" is a stub rather than real code.

    Shipping "// existing logic" into a Jira ticket looks like a confident fix
    and is worse than saying nothing, so the ticket says plainly that the
    current code block is the authoritative one.
    """
    if not code:
        return True
    lowered = code.lower()
    markers = ("// existing", "// todo", "...", "// your code", "omitted",
               "// rest of", "// unchanged", "implement this", "// logic")
    if any(m in lowered for m in markers):
        return True
    # A real method body has braces and at least a few statements.
    return code.count(";") < 1 and code.count("{") < 1


def build_issue_summary(pack: dict, fix: dict, source_issue_key: str = None) -> str:
    """One-line ticket title a human can scan in a board list."""
    affected = pack["affected"]
    incident = affected.get("incident")
    # Only cite the source ticket when it is a DIFFERENT issue — otherwise
    # "raised from INC-10101" on a ticket whose own subject is INC-10101 is noise.
    origin = source_issue_key if (source_issue_key and source_issue_key != incident) else None
    what = incident or affected.get("order") or "incident"
    kind = "Code fix" if fix else (pack.get("fix_type") or "Investigation")
    tail = f"raised from {origin}" if origin else "raised by BugZero"
    return f"[BugZero] {kind}: {what} — {tail}"[:255]


def build_issue_description(pack: dict, fix: dict, inv: dict,
                            source_issue_key: str = None,
                            source_link: str = None) -> str:
    """The Jira body. Evidence first, claim second, always marked as a proposal."""
    a = pack["affected"]
    lines = [
        "BugZero investigator report — PROPOSAL ONLY, human review required.",
        "",
        "## Summary",
        pack["summary"] or "_No summary — the LLM did not run._",
        "",
        "## Root cause",
        pack["root_cause"] or "_Not determined._",
        "",
        f"**Classification:** {pack['fix_type']} · **Confidence:** {pack['confidence']}",
        "",
        "## Affected",
        f"- Source incident: {a.get('incident') or '—'}",
        f"- Order: {a.get('order') or '—'} (status {a.get('order_status') or '—'})",
        f"- Customer: {a.get('customer') or '—'}",
        f"- Services holding failed rows: {', '.join(a.get('services')) or '—'}",
        "",
        "## Evidence",
    ]
    if pack["correlations"]:
        lines.append(f"Correlation IDs: {', '.join(pack['correlations'])}")
    stats = pack.get("log_stats") or {}
    if stats:
        lines.append(f"Correlated log lines: {stats.get('lines', 0)} "
                     f"({stats.get('direct_hits', 0)} direct hits)")
    if pack["db_tables"]:
        lines.append(f"Database tables read: {', '.join(pack['db_tables'])}")
    lines += ["", "### Proof lines", "```"]
    lines += pack["evidence_refs"] or ["_none captured_"]
    lines += ["```", ""]

    if pack["journey"]:
        lines += ["### Service journey", "| Time | Service | Event | Status |",
                  "|---|---|---|---|"]
        for j in pack["journey"]:
            lines.append(f"| {j.get('time','—')} | {j.get('service','—')} "
                         f"| {j.get('event','—')} | {j.get('status','—')} |")
        lines.append("")

    if fix:
        lines += [
            "## Suggested code location",
            "```",
            _location_text(fix.get("location")),
            "```",
            "",
            f"**Service:** {fix.get('service')} · **Class:** {fix.get('class_name')} "
            f"· **Method:** {fix.get('method')}",
            "",
            "## Proposed change",
            "**Current code (verbatim from source)**",
            "```java",
            (fix.get("before") or "—")[:4000],
            "```",
            "",
            "**Suggested replacement**",
            "```java",
            (fix.get("after") or "—")[:4000],
            "```",
            "",
        ]
        if not fix.get("verified"):
            lines += ["> The method name above was not found in the checked-out source. "
                      "Verify before acting.", ""]
        if fix.get("before_from_source") and _is_placeholder(fix.get("after")):
            lines += ["> The suggested replacement above is incomplete — it was not "
                      "written against the real method body. Treat the current code "
                      "block as authoritative and the change as a starting point.", ""]

    if pack["next_steps"]:
        lines += ["## Suggested next steps"]
        lines += [f"{i}. {s}" for i, s in enumerate(pack["next_steps"], 1)]
        lines.append("")

    if source_issue_key:
        lines += ["## Origin", f"- Source incident: {source_issue_key}"]
        if source_link:
            lines.append(f"- {source_link}")
        lines.append("")

    lines += ["---", codefix.DISCLAIMER]
    return "\n".join(lines)


def build_email(pack: dict, fix: dict, jira_key: str = None, jira_url: str = None) -> tuple:
    """(subject, plain-text body) for the developer notification."""
    a = pack["affected"]
    subject = (f"BugZero: {pack['fix_type']} fix indicated for "
               f"{a.get('order') or a.get('incident') or 'incident'}"
               + (f" ({jira_key})" if jira_key else ""))
    lines = [
        "BugZero finished an investigation and found a problem that needs a code change.",
        "",
        f"SUMMARY: {pack['summary'] or '—'}",
        f"ROOT CAUSE: {pack['root_cause'] or '—'}",
        f"CLASSIFICATION: {pack['fix_type']} · CONFIDENCE: {pack['confidence']}",
        "",
        f"AFFECTED: order {a.get('order') or '—'} (status {a.get('order_status') or '—'}), "
        f"services {', '.join(a.get('services')) or '—'}",
        "",
    ]
    if jira_key:
        lines += [f"JIRA: {jira_key}" + (f" — {jira_url}" if jira_url else ""), ""]
    lines += ["EVIDENCE"]
    if pack["correlations"]:
        lines.append(f"  correlation IDs: {', '.join(pack['correlations'])}")
    for ref in (pack["evidence_refs"] or [])[:6]:
        lines.append(f"  - {ref}")
    lines.append("")
    if fix:
        lines += [
            "SUGGESTED CODE LOCATION",
            f"  {_location_text(fix.get('location')).splitlines()[0]}",
            f"  class {fix.get('class_name')} / method {fix.get('method')}",
            "",
        ]
    if pack["next_steps"]:
        lines.append("SUGGESTED NEXT STEPS")
        lines += [f"  {i}. {s}" for i, s in enumerate(pack["next_steps"], 1)]
        lines.append("")
    lines += ["---", codefix.DISCLAIMER]
    return subject[:250], "\n".join(lines)


def handoff(inv: dict, source_issue_key: str = None, send_email: bool = True,
            create_ticket: bool = True, to_addrs=None, extra_labels: list = None) -> dict:
    """Build the full handoff and (optionally) transmit it.

    Returns the rendered artifacts ALWAYS plus the transmission result for each
    channel, so the console can show exactly what would go out even when Jira
    or SMTP is unconfigured.
    """
    pack = _evidence_pack(inv)
    fix = _code_fix_block(inv)

    summary = build_issue_summary(pack, fix, source_issue_key)
    description = build_issue_description(pack, fix, inv, source_issue_key)

    labels = list(BASE_LABELS)
    if fix:
        labels.append("code-fix")
    if pack["fix_type"].upper() == "DATA":
        labels.append("data-fix")
    if (pack["confidence"] or "low").lower() == "low":
        # Low-confidence work must not sit in the "act now" lane.
        labels.append("needs-review")
    labels += [l for l in (extra_labels or []) if l]
    slugged = sorted({l.strip().replace(" ", "-").lower() for l in labels if l.strip()})

    subject, body = build_email(pack, fix)

    result = {
        "source_issue": source_issue_key,
        "issue_summary": summary,
        "issue_description": description,
        "issue_labels": slugged,
        "issue_priority": _confidence_priority(pack["confidence"]),
        "email_subject": subject,
        "email_body": body,
        "evidence_pack": pack,
        "code_fix": fix,
        "code_location_verified": bool(fix.get("verified")),
        "jira": {"attempted": False, "ok": False},
        "email": {"attempted": False, "ok": False},
        "safety": ("Writes to Jira and email only. Never changes application data, "
                   "never commits or deploys code."),
    }

    if create_ticket:
        result["jira"]["attempted"] = True
        created = jira_client.create_issue(
            summary=summary,
            description=description,
            labels=slugged,
            priority=result["issue_priority"],
        )
        result["jira"].update(created)
        if created.get("ok"):
            result["email_subject"], result["email_body"] = build_email(
                pack, fix, created.get("key"), created.get("url"))
            # Join the fix ticket back to the incident it came from.
            if source_issue_key and source_issue_key != created.get("key"):
                link = jira_client.link_issues(source_issue_key, created["key"])
                result["jira"]["link"] = link

    if send_email:
        result["email"]["attempted"] = True
        sent = mailer.send(result["email_subject"], result["email_body"], to_addrs)
        result["email"].update(sent)

    return result


def comment_on_issue(issue_key: str, inv: dict) -> dict:
    """Post a re-investigation's fresh evidence onto the original ticket.

    This is the closing arc of slide 3 — the developer fixes it, the fix is
    verified, and the original incident carries the evidence that closed it.
    """
    pack = _evidence_pack(inv)
    fix = _code_fix_block(inv)
    lines = [
        "BugZero re-investigation update.",
        "",
        f"Summary: {pack['summary'] or '—'}",
        f"Root cause: {pack['root_cause'] or '—'}",
        f"Confidence: {pack['confidence']}",
        "",
        "Evidence:",
    ]
    lines += [f"- {r}" for r in (pack["evidence_refs"] or [])[:8]] or ["- none captured"]
    if fix:
        lines += ["", f"Suggested location: {_location_text(fix.get('location'))}",
                  "", "```java", (fix.get("after") or "—")[:3000], "```"]
    lines += ["", codefix.DISCLAIMER]
    return jira_client.add_comment(issue_key, "\n".join(lines))