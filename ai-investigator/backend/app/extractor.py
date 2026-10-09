"""Entity extraction from a Jira-style incident text (Phase 14).

Pulls every identifier the investigator can pivot on: incident/order/customer/
request/reservation/notification IDs, timestamps, status words and error tokens.
Pure regex — no LLM needed for this step, fully deterministic.
"""

import re

PATTERNS = {
    "incident_id": r"\bINC-\d{4,}\b",
    "order_number": r"\bORD-[A-Z0-9]{4,}\b",
    "customer_number": r"\bCUS-[A-Z0-9]{4,}\b",
    "request_number": r"\bPRV-[A-Z0-9]{4,}\b",
    "reservation_number": r"\bRSV-[A-Z0-9]{4,}\b",
    "notification_number": r"\bNTF-[A-Z0-9]{4,}\b",
    "resource_number": r"\bRES-[A-Z0-9]{4,}\b",
    "correlation_id": r"\b[A-Z]+-[A-Z]+-\d{3,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    "timestamp": r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b",
    "order_id": r"\border\s+(\d+)\b",
    "customer_id": r"\bcustomer\s+(\d+)\b",
}

STATUS_WORDS = [
    "CREATED", "VALIDATING", "VALIDATED", "PAYMENT_PENDING", "PAYMENT_COMPLETED",
    "INVENTORY_RESERVED", "PROVISIONING", "ACTIVATING", "COMPLETED", "FAILED",
    "CANCELLED", "ROLLING_BACK", "RETRYING", "PENDING", "IN_PROGRESS", "SENT",
    "RESERVED", "ALLOCATED", "AVAILABLE", "QUARANTINED", "EXPIRED", "ACTIVE",
    "SUSPENDED", "BLOCKED",
]

ERROR_HINTS = [
    "MISSING_MSISDN", "MISSING_RESOURCE", "MISSING_IDENTIFIER", "INVENTORY_SHORTAGE",
    "CUSTOMER_INVALID", "CUSTOMER_NOT_FOUND", "CUSTOMER_SERVICE_UNAVAILABLE",
    "stuck", "expired", "leak", "duplicate", "mismatch", "failed", "rejected",
    "timeout", "breach", "overcharge", "refused",
]


def extract(text: str) -> dict:
    """Return {field: [unique matches in order]} plus status/error keyword hits."""
    entities: dict = {}
    for field, pattern in PATTERNS.items():
        found: list = []
        for m in re.finditer(pattern, text, re.IGNORECASE if field in ("order_id", "customer_id") else 0):
            val = m.group(1) if field in ("order_id", "customer_id") else m.group(0)
            if val not in found:
                found.append(val)
        if found:
            entities[field] = found
    upper = text.upper()
    statuses = [w for w in STATUS_WORDS if re.search(r"\b" + w + r"\b", upper)]
    if statuses:
        entities["status_words"] = statuses
    errors = [w for w in ERROR_HINTS if w.lower() in text.lower()]
    if errors:
        entities["error_hints"] = errors
    return entities
