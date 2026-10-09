"""Read-only database evidence (Phase 14).

The investigator NEVER writes: every query is a SELECT against the service-owned
databases, using the same POSTGRES_* env the services use. Human approval stays
mandatory for any fix (Phases 16-17).
"""

import os
import re

DSN = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
}

DBS = {
    "customer": os.environ.get("CUSTOMER_DB_NAME", "telecom_customer"),
    "order": os.environ.get("ORDER_DB_NAME", "telecom_order"),
    "inventory": os.environ.get("INVENTORY_DB_NAME", "telecom_inventory"),
    "provisioning": os.environ.get("PROVISIONING_DB_NAME", "telecom_provisioning"),
    "notification": os.environ.get("NOTIFICATION_DB_NAME", "telecom_notification"),
}


def _connect(db: str):
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dbname=DBS[db], **DSN)
    return conn


def _select(db: str, sql: str, params: tuple):
    try:
        conn = _connect(db)
    except Exception as e:  # DB down => evidence unavailable, not fatal
        return {"error": f"{db}: {e}"}
    try:
        with conn:
            with conn.cursor() as cur:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(sql, params)
                rows = cur.fetchall()
        return {"rows": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": f"{db}: {e}"}
    finally:
        conn.close()


def collect(entities: dict, log_result: dict) -> dict:
    """Pull DB state for every extracted identifier. SELECT only."""
    evidence: dict = {}
    order_ids = set(entities.get("order_id", []))

    # order numbers -> ids (log lines often carry orderId=N directly)
    for num in entities.get("order_number", []):
        r = _select("order", "SELECT id, order_number, status FROM telecom_order WHERE order_number = %s", (num,))
        for row in r.get("rows", []):
            order_ids.add(str(row["id"]))
            evidence.setdefault("orders", []).append(row)

    for oid in order_ids:
        if not str(oid).isdigit():
            continue
        o = _select("order", "SELECT id, order_number, status FROM telecom_order WHERE id = %s", (int(oid),))
        evidence.setdefault("orders", []).extend(o.get("rows", []))
        h = _select("order",
                    "SELECT event, from_status, to_status, comment, created_at FROM order_history "
                    "WHERE order_id = %s ORDER BY created_at", (int(oid),))
        if h.get("rows"):
            evidence.setdefault("order_history", []).extend(h["rows"])
        p = _select("order",
                    "SELECT payment_reference, amount, method, status FROM payment WHERE order_id = %s", (int(oid),))
        if p.get("rows"):
            evidence.setdefault("payments", []).extend(p["rows"])
        pr = _select("provisioning",
                     "SELECT request_number, service_type, status, last_error FROM provisioning_request WHERE order_id = %s",
                     (int(oid),))
        if pr.get("rows"):
            evidence.setdefault("provisioning", []).extend(pr["rows"])
        rv = _select("inventory",
                     "SELECT r.reservation_number, r.status, res.resource_number, res.status AS resource_status "
                     "FROM resource_reservation r JOIN inventory_resource res ON res.id = r.resource_id "
                     "WHERE r.order_id = %s", (int(oid),))
        if rv.get("rows"):
            evidence.setdefault("reservations", []).extend(rv["rows"])
        nt = _select("notification",
                     "SELECT notification_number, channel, status FROM notification WHERE order_id = %s", (int(oid),))
        if nt.get("rows"):
            evidence.setdefault("notifications", []).extend(nt["rows"])

    for num in entities.get("request_number", []):
        r = _select("provisioning",
                    "SELECT request_number, service_type, status, last_error FROM provisioning_request WHERE request_number = %s",
                    (num,))
        evidence.setdefault("provisioning", []).extend(r.get("rows", []))

    for num in entities.get("reservation_number", []):
        r = _select("inventory",
                    "SELECT r.reservation_number, r.status, res.resource_number, res.status AS resource_status "
                    "FROM resource_reservation r JOIN inventory_resource res ON res.id = r.resource_id "
                    "WHERE r.reservation_number = %s", (num,))
        evidence.setdefault("reservations", []).extend(r.get("rows", []))

    for num in entities.get("notification_number", []):
        r = _select("notification",
                    "SELECT notification_number, channel, status, last_error FROM notification WHERE notification_number = %s",
                    (num,))
        evidence.setdefault("notifications", []).extend(r.get("rows", []))

    # dedupe orders
    if "orders" in evidence:
        seen, uniq = set(), []
        for o in evidence["orders"]:
            if o["id"] not in seen:
                seen.add(o["id"])
                uniq.append(o)
        evidence["orders"] = uniq
    for key, idkey in (("provisioning", "request_number"),
                       ("reservations", "reservation_number"),
                       ("notifications", "notification_number")):
        if key in evidence:
            seen, uniq = set(), []
            for r in evidence[key]:
                if r.get(idkey) not in seen:
                    seen.add(r.get(idkey))
                    uniq.append(r)
            evidence[key] = uniq
    return evidence


def resolve_order_ids(entities: dict) -> dict:
    """Map every extracted number to numeric order ids BEFORE log search, since
    log lines carry orderId=N (numeric) but almost never the PRV-/RSV-/NTF- codes."""
    ids = set(entities.get("order_id", []))
    for num in entities.get("order_number", []):
        r = _select("order", "SELECT id FROM telecom_order WHERE order_number = %s", (num,))
        for row in r.get("rows", []):
            ids.add(str(row["id"]))
    for num in entities.get("request_number", []):
        r = _select("provisioning", "SELECT order_id FROM provisioning_request WHERE request_number = %s", (num,))
        for row in r.get("rows", []):
            if row.get("order_id") is not None:
                ids.add(str(row["order_id"]))
    for num in entities.get("reservation_number", []):
        r = _select("inventory", "SELECT order_id FROM resource_reservation WHERE reservation_number = %s", (num,))
        for row in r.get("rows", []):
            if row.get("order_id") is not None:
                ids.add(str(row["order_id"]))
    for num in entities.get("notification_number", []):
        r = _select("notification", "SELECT order_id FROM notification WHERE notification_number = %s", (num,))
        for row in r.get("rows", []):
            if row.get("order_id") is not None:
                ids.add(str(row["order_id"]))
    if ids:
        entities["order_id"] = sorted(ids)
    return entities


def has_identifiers(entities: dict) -> bool:
    """True when the ticket names at least one traceable ID."""
    for f in ("order_number", "order_id", "customer_number", "customer_id",
              "request_number", "reservation_number", "notification_number",
              "resource_number"):
        if entities.get(f):
            return True
    return False


def recent_candidates() -> dict:
    """Fresh leads for vague tickets: latest stuck/failed rows per domain."""
    out = {}
    o = _select("order",
                "SELECT id, order_number, status FROM telecom_order "
                "WHERE status IN ('FAILED','PAYMENT_PENDING','RETRYING') "
                "ORDER BY id DESC LIMIT 5", ())
    if o.get("rows"):
        out["suspicious_orders"] = o["rows"]
    p = _select("provisioning",
                "SELECT request_number, service_type, status, last_error FROM provisioning_request "
                "WHERE status = 'FAILED' ORDER BY id DESC LIMIT 5", ())
    if p.get("rows"):
        out["failed_provisioning"] = p["rows"]
    n = _select("notification",
                "SELECT notification_number, channel, status, last_error FROM notification "
                "WHERE status = 'FAILED' ORDER BY id DESC LIMIT 5", ())
    if n.get("rows"):
        out["failed_notifications"] = n["rows"]
    r = _select("inventory",
                "SELECT r.reservation_number, r.status, res.resource_number, res.status AS resource_status "
                "FROM resource_reservation r JOIN inventory_resource res ON res.id = r.resource_id "
                "WHERE r.status = 'ACTIVE' ORDER BY r.id DESC LIMIT 5", ())
    if r.get("rows"):
        out["open_reservations"] = r["rows"]
    return out
