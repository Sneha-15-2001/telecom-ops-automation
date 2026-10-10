"""Incident scenario cards as first-class tickets.

The ``incident-scenarios/scenario-*/INC-*.md`` files are Jira-style tickets, but
nothing read them: the investigator's ticket queue was built purely from
database state, so the scenario library was invisible to the console.

That gap matters most for the platform-failure scenarios (501/502/503/429).
Those leave an order in a *normal* state — CREATED, or PAYMENT_COMPLETED and
retryable — because a dependency outage is transient and must not fail the
order. Nothing in the database marks them, so a DB-derived queue can never
surface them. The card is the ticket.

Each card is parsed into the same shape ``/api/incidents`` already returns, so
the console needs no special case.
"""

import re
from pathlib import Path

SCENARIO_ROOT = Path(__file__).resolve().parents[3] / "incident-scenarios"

# Only these need fault injection armed; the rest reproduce by calling APIs.
NEEDS_CHAOS = {15, 16, 21}

PRIORITY_BY_NUMBER = {
    1: "P2", 2: "P2", 3: "P2", 4: "P2", 5: "P3", 6: "P3", 7: "P3", 8: "P3",
    9: "P3", 10: "P2", 11: "P3", 12: "P2", 13: "P3", 14: "P3",
    15: "P1", 16: "P1", 17: "P2", 18: "P2", 19: "P1", 20: "P3", 21: "P1", 22: "P3",
}

HTTP_CODE_BY_NUMBER = {
    15: "502 Bad Gateway",
    16: "503 Service Unavailable",
    17: "501 Not Implemented",
    18: "429 Too Many Requests",
    19: "500 Internal Server Error",
    20: "409 Conflict",
    21: "502 Bad Gateway",
    22: "415 Unsupported Media Type / 405 Method Not Allowed",
}


def _strip_markdown(text: str) -> str:
    """Flatten card prose into something an investigator can read as a ticket."""
    text = re.sub(r"^#+\s*", "", text, flags=re.M)          # headings
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)            # bold
    text = re.sub(r"`([^`]*)`", r"\1", text)                # inline code
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)     # bullets
    text = re.sub(r"```.*?```", "", text, flags=re.S)        # code blocks
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _field(body: str, label: str) -> str:
    """Pull '- **Symptom:** ...' style metadata out of the card."""
    m = re.search(r"^\s*[-*]\s*\*\*%s:?\*\*:?\s*(.+)$" % re.escape(label),
                  body, flags=re.M | re.I)
    return m.group(1).strip() if m else ""


def _number_from_key(key: str) -> int:
    m = re.search(r"INC-101(\d+)", key)
    return int(m.group(1)) if m else 0


def load() -> list:
    """Every scenario card, as tickets the console can list and investigate."""
    if not SCENARIO_ROOT.exists():
        return []
    out = []
    for card in sorted(SCENARIO_ROOT.glob("scenario-*/INC-*.md")):
        try:
            raw = card.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        h1 = re.search(r"^#\s+(.+)$", raw, flags=re.M)
        if not h1:
            continue
        title = h1.group(1).strip()
        key = re.match(r"(INC-\d+)", title)
        if not key:
            continue
        key = key.group(1)
        num = _number_from_key(key)

        # Drop the H1 line: it becomes the title, and repeating it in the body
        # wastes evidence budget the investigator spends on real log lines.
        body = raw.split("\n", 1)[1] if "\n" in raw else ""
        symptom = _field(body, "Symptom")
        reported = _field(body, "Reported")
        suspected = _field(body, "Suspected area")
        status_field = _field(body, "Status") or "Open (P2)"

        priority = PRIORITY_BY_NUMBER.get(num, "P2")
        http_code = HTTP_CODE_BY_NUMBER.get(num)

        parts = [
            f"Jira {key}: {title.split('—', 1)[-1].strip() if '—' in title else title}",
            f"Priority: {priority}.",
        ]
        if http_code:
            parts.append(f"Observed HTTP status: {http_code}.")
        if symptom:
            parts.append(f"Symptom: {symptom}")
        if suspected:
            parts.append(f"Suspected area: {suspected}")
        if reported:
            parts.append(f"Reported by: {reported}")
        parts.append("")
        parts.append("Reported narrative:\n" + _strip_markdown(body)[:4000])

        out.append({
            "id": key,
            "title": title.split("—", 1)[-1].strip() if "—" in title else title,
            "status": status_field.replace("Open", "Open").strip(),
            "priority": priority,
            "reporter": reported.split("—")[0].strip() if reported else "Scenario library",
            "source": "scenario",
            "kind": "http-platform" if http_code else "domain",
            "http_status": http_code,
            "requires_chaos": num in NEEDS_CHAOS,
            "order": None,
            "error": http_code or symptom,
            # The text the investigator actually reasons over.
            "text": "\n".join(parts),
            "card_path": str(card.relative_to(SCENARIO_ROOT.parent)),
        })
    return out


def get(key: str) -> dict:
    """One card by ticket key, for investigating straight from the console."""
    for t in load():
        if t["id"].upper() == (key or "").upper():
            return t
    return {}