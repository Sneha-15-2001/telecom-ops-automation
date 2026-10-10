"""BugZero's own investigation history (PostgreSQL).

Everything else in this app is read-only against the five service databases.
This module is the ONE place BugZero writes, and it writes only about itself:
which investigations ran, what they concluded, and what was handed to a
developer. It never writes to telecom_* schemas.

Separate database (BUGZERO_DB_NAME, default bugzero) so the investigator's
history can never be confused with — or backed up alongside — customer data.

Every function degrades: if PostgreSQL is unreachable, callers get
{ok: False, error} and the console falls back to in-memory session history.
An investigation that produced good evidence must not be lost because the
history store is down.
"""

import json
import os
from datetime import datetime, timezone

DB_NAME = os.environ.get("BUGZERO_DB_NAME", "bugzero")

DSN = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigation (
    id              SERIAL PRIMARY KEY,
    reference       TEXT UNIQUE NOT NULL,
    source_issue    TEXT,
    incident_text   TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'llm',
    status          TEXT NOT NULL DEFAULT 'COMPLETED',
    verdict_summary TEXT,
    verdict_root    TEXT,
    fix_type        TEXT,
    confidence      TEXT,
    evidence_pack   JSONB,
    entity_json     JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_investigation_created  ON investigation (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_investigation_source   ON investigation (source_issue);
CREATE INDEX IF NOT EXISTS idx_investigation_status   ON investigation (status);

CREATE TABLE IF NOT EXISTS handoff (
    id              SERIAL PRIMARY KEY,
    investigation_id INTEGER REFERENCES investigation(id) ON DELETE SET NULL,
    reference       TEXT UNIQUE NOT NULL,
    source_issue    TEXT,
    jira_key        TEXT,
    jira_url        TEXT,
    email_sent      BOOLEAN NOT NULL DEFAULT FALSE,
    email_recipients TEXT,
    channel_status  JSONB,
    evidence_pack   JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_handoff_created ON handoff (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_handoff_jira    ON handoff (jira_key);

-- Slide 3 closes the loop: the developer fixes it, we re-investigate, and the
-- original investigation records its own outcome.
CREATE TABLE IF NOT EXISTS followup (
    id                SERIAL PRIMARY KEY,
    investigation_id  INTEGER REFERENCES investigation(id) ON DELETE CASCADE,
    note              TEXT,
    new_status        TEXT,
    jira_key          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _connect():
    import psycopg2
    return psycopg2.connect(dbname=DB_NAME, **DSN)


def ensure_schema() -> dict:
    """Create BugZero's tables if absent. Safe to call on every start."""
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
        return {"ok": True, "schema": "ready", "database": DB_NAME}
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    finally:
        conn.close()


def _ref(prefix: str) -> str:
    """Human-scannable reference, the same shape the services use (ORD-…)."""
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:14]}"


def _json(value) -> str:
    return json.dumps(value or {}, default=str)


def save_investigation(inv: dict, source_issue: str = None, status: str = "COMPLETED",
                       error: str = None) -> dict:
    """Persist one investigation and return {ok, id, reference}."""
    verdict = inv.get("verdict") or {}
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    ref = _ref("BZI")
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO investigation
                       (reference, source_issue, incident_text, mode, status,
                        verdict_summary, verdict_root, fix_type, confidence,
                        evidence_pack, entity_json, error)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                       RETURNING id""",
                    (ref, source_issue, inv.get("incident_text", ""),
                     inv.get("mode", "llm"), status,
                     verdict.get("summary"), verdict.get("root_cause"),
                     verdict.get("fix_type"), verdict.get("confidence"),
                     _json({"log_stats": inv.get("log_stats", {}),
                            "correlations": inv.get("correlations", []),
                            "db_tables": sorted((inv.get("db_evidence") or {}).keys())}),
                     _json(inv.get("entities", {})),
                     error))
                new_id = cur.fetchone()[0]
        return {"ok": True, "id": new_id, "reference": ref}
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    finally:
        conn.close()


def save_handoff(hand: dict, investigation_id: int = None) -> dict:
    """Persist one developer handoff, including which channels succeeded."""
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    ref = _ref("BZH")
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO handoff
                       (investigation_id, reference, source_issue, jira_key, jira_url,
                        email_sent, email_recipients, channel_status, evidence_pack)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                       RETURNING id""",
                    (investigation_id, ref, hand.get("source_issue"),
                     (hand.get("jira") or {}).get("key"),
                     (hand.get("jira") or {}).get("url"),
                     bool((hand.get("email") or {}).get("sent")),
                     ", ".join((hand.get("email") or {}).get("to", []) or []),
                     _json({"jira": hand.get("jira"), "email": hand.get("email")}),
                     _json(hand.get("evidence_pack", {}))))
                new_id = cur.fetchone()[0]
        return {"ok": True, "id": new_id, "reference": ref}
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    finally:
        conn.close()


def add_followup(investigation_id: int, note: str, new_status: str = None,
                 jira_key: str = None) -> dict:
    """Record the developer's outcome against the original investigation."""
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO followup (investigation_id, note, new_status, jira_key)
                       VALUES (%s,%s,%s,%s) RETURNING id""",
                    (investigation_id, note, new_status, jira_key))
                new_id = cur.fetchone()[0]
                if new_status:
                    cur.execute("UPDATE investigation SET status = %s WHERE id = %s",
                                (new_status, investigation_id))
        return {"ok": True, "id": new_id}
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    finally:
        conn.close()


def list_investigations(limit: int = 50, status: str = None,
                        source_issue: str = None) -> dict:
    """Newest-first history for the console."""
    clauses, params = [], []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if source_issue:
        clauses.append("source_issue = %s")
        params.append(source_issue)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}", "investigations": []}
    try:
        with conn:
            with conn.cursor() as cur:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    f"""SELECT i.id, i.reference, i.source_issue, i.mode, i.status,
                               i.verdict_summary, i.fix_type, i.confidence,
                               i.created_at, i.error,
                               h.jira_key, h.email_sent
                          FROM investigation i
                          LEFT JOIN handoff h ON h.investigation_id = i.id
                          {where}
                          ORDER BY i.created_at DESC
                          LIMIT %s""",
                    params + [max(1, min(int(limit), 500))])
                rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
        return {"ok": True, "count": len(rows), "investigations": rows}
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}", "investigations": []}
    finally:
        conn.close()


def get_investigation(reference: str) -> dict:
    """One investigation with its evidence pack, for the history detail view."""
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    try:
        with conn:
            with conn.cursor() as cur:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    """SELECT i.*, h.reference AS handoff_reference, h.jira_key, h.jira_url,
                              h.email_sent, h.email_recipients
                         FROM investigation i
                         LEFT JOIN handoff h ON h.investigation_id = i.id
                        WHERE i.reference = %s OR i.id::text = %s LIMIT 1""",
                    (reference, str(reference)))
                row = cur.fetchone()
        if not row:
            return {"ok": False, "error": f"No investigation {reference}"}
        out = dict(row)
        for k in ("created_at",):
            out[k] = out[k].isoformat() if out.get(k) else None
        return {"ok": True, "investigation": out}
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    finally:
        conn.close()


def stats() -> dict:
    """Headline counts for the console dashboard."""
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT count(*) AS investigations,
                                      count(*) FILTER (WHERE status='COMPLETED') AS completed,
                                      count(*) FILTER (WHERE confidence='high') AS high_confidence
                                 FROM investigation""")
                top = cur.fetchone()
                cur.execute("""SELECT count(*) FROM handoff WHERE jira_key IS NOT NULL""")
                tickets = cur.fetchone()[0]
                cur.execute("""SELECT count(*) FROM handoff WHERE email_sent""")
                emails = cur.fetchone()[0]
        return {"ok": True, "investigations": top[0], "completed": top[1],
                "high_confidence": top[2], "tickets_created": tickets,
                "emails_sent": emails}
    except Exception as e:
        return {"ok": False, "error": f"{DB_NAME}: {e}"}
    finally:
        conn.close()


def health() -> dict:
    """Connectivity for the console's status row."""
    try:
        conn = _connect()
        conn.close()
        return {"configured": True, "reachable": True, "database": DB_NAME,
                "detail": "connected"}
    except Exception as e:
        return {"configured": True, "reachable": False, "database": DB_NAME,
                "detail": str(e)[:200]}