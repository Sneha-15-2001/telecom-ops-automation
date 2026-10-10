"""Targeted log search — never dumps whole files.

Strategy (mirrors scripts/search-logs.sh, but bounded for API use):
1. Scan each service's app.log for lines matching any extracted identifier.
2. Harvest correlation IDs from those hits.
3. Expand: pull every line carrying those correlation IDs (cap 300).
4. Sort by timestamp, derive the service journey (service → event → status).

Three sources, tried in order — remote SSH (production hosts), the local log
HTTP server, then local files. Each returns the same [{service, line}] shape, so
the search logic above is identical regardless of where the bytes came from.
"""

import functools
import os
import re
from pathlib import Path

from . import ssh_collector

SERVICES = [
    "customer-service",
    "order-service",
    "inventory-service",
    "provisioning-service",
    "notification-service",
]

LOG_ROOT = Path(os.environ.get(
    "INVESTIGATOR_LOG_ROOT",
    # .../ai-investigator/backend/app/logsearch.py -> parents[3] is the repo root
    str(Path(__file__).resolve().parents[3] / "logs"),
))

# Pull mode: fetch log lines over HTTP from the 127.0.0.1 log server
# (Phase 14c "store on localhost, pull" pattern) instead of reading files.
# Set INVESTIGATOR_LOG_PULL=http://127.0.0.1:8899 to enable. Unset = file mode.
LOG_PULL_BASE = os.environ.get("INVESTIGATOR_LOG_PULL", "").rstrip("/")

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
CORR_RE = re.compile(r"\[([A-Za-z0-9_.\-]+),([A-Za-z0-9_.\-]+)\]")
EVENT_RE = re.compile(r"event=([A-Z_]+)")
STATUS_RE = re.compile(r"(?:^|\s)status=([A-Z_]+)")

MAX_EXPANDED = 300


def _iter_lines(service: str):
    if LOG_PULL_BASE:
        yield from _iter_lines_http(service)
        return
    # Current file PLUS rolled .gz archives — rotation must never hide evidence.
    paths = sorted((LOG_ROOT / service).glob("app.*.log.gz"))
    logs_now = LOG_ROOT / service / "app.log"
    if logs_now.exists():
        paths.append(logs_now)
    import gzip
    for path in paths:
        try:
            if path.suffix == ".gz":
                fh = gzip.open(path, "rt", encoding="utf-8", errors="replace")
            else:
                fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                yield line.rstrip("\n")


@functools.lru_cache(maxsize=1)
def _ssh_snapshot():
    """One SSH pull per investigation, cached across all five services.

    Collection is the expensive step — one SFTP session beats five — so we fetch
    once and slice per service. Cached so a single investigate() call that walks
    each service twice (hits, then correlation expansion) does not re-transfer.
    """
    result = ssh_collector.collect()
    return result if result.get("ok") else None


def _reset_ssh_cache():
    """Drop the cached snapshot (after a write, or in tests)."""
    _ssh_snapshot.cache_clear()


def _iter_lines_http(service: str):
    import json
    import urllib.request
    url = f"{LOG_PULL_BASE}/api/logs/{service}/app.log"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for line in data.get("lines", []):
            yield line
    except Exception:
        return


def active_source() -> dict:
    """Which log source this process will actually read, for the console status row."""
    if ssh_collector.is_ssh_configured():
        snap = _ssh_snapshot()
        if snap:
            return {"mode": "ssh", "host": snap.get("host"),
                    "remote_root": snap.get("remote_root"),
                    "lines_available": len(snap.get("lines", [])),
                    "detail": "reading approved server over SSH"}
        return {"mode": "ssh", "host": ssh_collector.ssh_config()["host"],
                "lines_available": 0, "error": "SSH configured but unreachable"}
    if LOG_PULL_BASE:
        return {"mode": "http", "endpoint": LOG_PULL_BASE, "detail": "pulling log server"}
    return {"mode": "file", "root": str(LOG_ROOT), "detail": "reading local files"}


def search(entities: dict) -> dict:
    needles = []
    for field in ("order_number", "customer_number", "request_number",
                  "reservation_number", "notification_number", "resource_number",
                  "correlation_id"):
        for v in entities.get(field, []):
            needles.append(str(v))
    # Numeric ids only ever match in qualified form (orderId=27), never bare:
    # bare "27" would hit timestamps and ports; and orderId=2 must not match
    # orderId=21/27/28, hence the (?!\d) boundary.
    id_res = []
    for v in entities.get("order_id", []):
        id_res.append(re.compile(rf"orderId={re.escape(str(v))}(?!\d)"))
    for v in entities.get("customer_id", []):
        id_res.append(re.compile(rf"customerId={re.escape(str(v))}(?!\d)"))
    needles = [n for n in needles if n]

    def _hit(low: str, line: str) -> bool:
        if any(n.lower() in low for n in needles):
            return True
        return any(rx.search(line) for rx in id_res)

    hits: list = []
    correlations: set = set()
    source = active_source()

    def _lines(service: str):
        """Yield raw lines from the active source for one service."""
        if source["mode"] == "ssh":
            snap = _ssh_snapshot()
            if snap:
                for h in snap["lines"]:
                    if h["service"] == service:
                        yield h["line"]
            return
        yield from _iter_lines(service)

    if needles or id_res:
        for svc in SERVICES:
            for line in _lines(svc):
                if _hit(line.lower(), line):
                    hits.append({"service": svc, "line": line})
                    m = CORR_RE.search(line)
                    if m and m.group(2) != "none":
                        correlations.add(m.group(2))

    expanded: list = []
    if correlations:
        for svc in SERVICES:
            for line in _lines(svc):
                m = CORR_RE.search(line)
                if m and m.group(2) in correlations:
                    expanded.append({"service": svc, "line": line})
                    if len(expanded) >= MAX_EXPANDED:
                        break
            if len(expanded) >= MAX_EXPANDED:
                break

    pool = expanded or hits
    pool = sorted(pool, key=lambda h: (TS_RE.search(h["line"] or "").group(1)
                                       if TS_RE.search(h["line"] or "") else ""))

    journey = []
    for h in pool:
        ev = EVENT_RE.search(h["line"])
        st = STATUS_RE.search(h["line"])
        if ev:
            journey.append({
                "service": h["service"],
                "event": ev.group(1),
                "status": st.group(1) if st else "-",
                "time": TS_RE.search(h["line"]).group(1) if TS_RE.search(h["line"]) else "",
            })

    return {
        "needles": needles,
        "correlations": sorted(correlations),
        "direct_hits": len(hits),
        "log_lines": pool[:MAX_EXPANDED],
        "journey": journey,
        "source": source,
    }
