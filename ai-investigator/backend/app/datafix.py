"""Temporary data-fix recommender (Phase 16).

Proposes minimal, validated SQL (or safe API operations) to unblock the
affected rows NOW. Proposals only — the investigator has no write path:
every proposal carries a validation SELECT, a rollback note, and
requires_approval=True. A human runs it, or nobody does.
"""

DISCLAIMER = ("PROPOSAL ONLY — human approval required. The investigator cannot "
              "execute fixes.")


def _res_rows(inv: dict):
    return (inv.get("db_evidence") or {}).get("reservations", [])


def _prov_rows(inv: dict):
    return (inv.get("db_evidence") or {}).get("provisioning", [])


def _notif_rows(inv: dict):
    return (inv.get("db_evidence") or {}).get("notifications", [])


def _order_rows(inv: dict):
    return (inv.get("db_evidence") or {}).get("orders", [])


def propose(inv: dict) -> dict:
    """Build data-fix proposals from investigation output."""
    suspect = (inv.get("suspect") or {}).get("service") or ""
    signal = ((inv.get("suspect") or {}).get("signal") or "").upper()
    fixes = []

    # 1. Leaked / expired reservation holding stock (INC-10103, INC-10110).
    for r in _res_rows(inv):
        if str(r.get("status", "")).upper() in ("ACTIVE", "EXPIRED"):
            num = r.get("reservation_number")
            res = r.get("resource_number", "?")
            fixes.append({
                "kind": "sql",
                "title": f"Free {res} held by {num}",
                "why": "Hold is dead (past TTL / dead order) yet the resource stays RESERVED; "
                       "freeing restores sellable stock without touching code.",
                "sql": [
                    f"-- 1. release the hold\nUPDATE resource_reservation SET status = 'CANCELLED' "
                    f"WHERE reservation_number = '{num}';",
                    f"-- 2. free the stock\nUPDATE inventory_resource SET status = 'AVAILABLE', "
                    f"order_id = NULL WHERE resource_number = '{res}';",
                ],
                "validation": (
                    f"SELECT r.reservation_number, r.status, res.resource_number, res.status "
                    f"FROM resource_reservation r JOIN inventory_resource res ON res.id = r.resource_id "
                    f"WHERE r.reservation_number = '{num}';  -- expect CANCELLED / AVAILABLE"),
                "rollback": ("Re-reserve only if the original order is still live; otherwise the "
                             "freed unit is simply sellable again — no reverse migration needed."),
                "requires_approval": True,
            })
            break  # one representative proposal; same shape repeats per row

    # 2. Payment recorded but never validated (INC-10101): no SQL — the money row
    #    is correct; the missing piece is an API call, proposed as an operation.
    for o in _order_rows(inv):
        if str(o.get("status", "")).upper() == "PAYMENT_PENDING":
            fixes.append({
                "kind": "api",
                "title": f"Complete validation for order {o.get('order_number')}",
                "why": "Data is correct (payment row PENDING is truthful); the skipped step is "
                       "operational, not a state error. No UPDATE needed.",
                "sql": [],
                "validation": (f"GET /api/orders/{o.get('id')}/payments — exactly one PENDING row; "
                               f"GET /api/orders/{o.get('id')}/history — no PAYMENT_VALIDATED event."),
                "rollback": "Not applicable — retry is idempotent; a declined gateway flips to FAILED instead.",
                "requires_approval": True,
                "operation": f"POST /api/orders/{o.get('id')}/payment/validate",
            })
            break

    # 3. Failed notification, retryable (INC-10104).
    for n in _notif_rows(inv):
        if str(n.get("status", "")).upper() == "FAILED":
            fixes.append({
                "kind": "api",
                "title": f"Retry notification {n.get('notification_number')}",
                "why": "Single failed attempt with a retryable path; body and recipient are intact.",
                "sql": [],
                "validation": (f"GET /api/notifications/<id>/attempts — expect a new attempt row; "
                               f"status SENT on a valid recipient."),
                "rollback": "If the retry also fails, cancel the notification; customer contact via care.",
                "requires_approval": True,
                "operation": "POST /api/notifications/<id>/retry (resolve <id> from notification_number first)",
            })
            break

    # 4. Duplicate notifications (INC-10105): cancel the extras, keep the first SENT.
    dupes = [n for n in _notif_rows(inv) if str(n.get("status", "")).upper() in ("PENDING", "RETRYING")]
    if len(dupes) > 1:
        keep = dupes[0].get("notification_number")
        drop = ", ".join(f"'{d.get('notification_number')}'" for d in dupes[1:3])
        fixes.append({
            "kind": "sql",
            "title": "Cancel duplicate notifications, keep the first",
            "why": "Duplicates are identical rows from a retried step; cancelling extras stops "
                   "further sends while preserving the audit trail.",
            "sql": [f"UPDATE notification SET status = 'CANCELLED' WHERE notification_number IN ({drop});"],
            "validation": (f"SELECT notification_number, status FROM notification WHERE "
                           f"notification_number IN ({drop});  -- expect CANCELLED; kept {keep} untouched"),
            "rollback": f"Flip back to PENDING if {keep} turns out undelivered.",
            "requires_approval": True,
        })

    # 5. Suspended-customer order (INC-10106): master data is correct — no fix,
    #    only a review action.
    if "SUSPENDED" in str(inv.get("entities", {}).get("status_words", [])) or "CUSTOMER" in signal:
        fixes.append({
            "kind": "review",
            "title": "Review the suspension before touching anything",
            "why": "The FAILED state is truthful: the customer really is SUSPENDED. Changing "
                   "order rows would falsify history.",
            "sql": [],
            "validation": "GET /api/customers/<id> — confirm suspension reason + date with master data.",
            "rollback": "Not applicable — no change proposed.",
            "requires_approval": True,
            "operation": "If suspension is stale: PATCH /api/customers/<id>/status, then reprocess the order.",
        })

    if not fixes:
        fixes.append({
            "kind": "review",
            "title": "No safe temporary data fix identified",
            "why": "Nothing in the evidence points at correctable rows; a blind UPDATE risks "
                   "falsifying history. Prefer the code path (Phase 17).",
            "sql": [],
            "validation": "Re-run the investigation with more identifiers.",
            "rollback": "Not applicable.",
            "requires_approval": True,
        })

    return {"disclaimer": DISCLAIMER, "fixes": fixes}
