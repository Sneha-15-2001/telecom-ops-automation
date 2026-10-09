"""Central log store on localhost (Phase 14c).

Mirrors the "store on localhost, pull over the network" pattern: the five
services keep writing their rolling files (Phase 12), and this tiny read-only
HTTP server on 127.0.0.1:8899 serves them to pull clients — the investigator
(Phase 14 pull-mode), humans via curl, or any SSH/SFTP-style collector later.

Run:  python3 server.py   (from ai-investigator/logserver)
API:
  GET /api/files                        -> ["customer-service/app.log", ...]
  GET /api/logs/<service>/app.log       -> full file (capped)
  GET /api/logs/<service>/app.log?grep=<text>&tail=200
Only stdlib. Binds 127.0.0.1 only — same-machine pulls, like David's demo host.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = int(os.environ.get("LOGSERVER_PORT", "8899"))
LOG_ROOT = Path(os.environ.get(
    "LOG_ROOT",
    Path(__file__).resolve().parents[2] / "logs",
)).resolve()

SERVICES = [
    "customer-service",
    "order-service",
    "inventory-service",
    "provisioning-service",
    "notification-service",
]

MAX_LINES = 2000


def _read(service: str, grep: str = "", tail: int = 0) -> list:
    if service not in SERVICES:
        return []
    import gzip
    paths = sorted((LOG_ROOT / service).glob("app.*.log.gz"))
    now = LOG_ROOT / service / "app.log"
    if now.exists():
        paths.append(now)
    out = []
    for path in paths:
        try:
            fh = gzip.open(path, "rt", encoding="utf-8", errors="replace") if path.suffix == ".gz" \
                else open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.rstrip("\n")
                if grep and grep.lower() not in line.lower():
                    continue
                out.append(line)
                if len(out) >= MAX_LINES:
                    break
        if len(out) >= MAX_LINES:
            break
    return out[-tail:] if tail > 0 else out


class Handler(BaseHTTPRequestHandler):
    server_version = "LogServer/0.1"

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json({"status": "UP", "service": "log-server", "root": str(LOG_ROOT)})
        if parsed.path == "/api/files":
            files = [f"{s}/app.log" for s in SERVICES if (LOG_ROOT / s / "app.log").exists()]
            return self._json({"root": str(LOG_ROOT), "files": files})
        parts = parsed.path.strip("/").split("/")
        # /api/logs/<service>/app.log
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "logs" and parts[3] == "app.log":
            qs = parse_qs(parsed.query)
            grep = qs.get("grep", [""])[0]
            try:
                tail = int(qs.get("tail", ["0"])[0])
            except ValueError:
                tail = 0
            lines = _read(parts[2], grep=grep, tail=tail)
            return self._json({"service": parts[2], "lines": lines, "count": len(lines)})
        return self._json({"error": "unknown path"}, 404)

    def log_message(self, *args):  # keep stdout clean
        pass


if __name__ == "__main__":
    print(f"log-server on http://{HOST}:{PORT} serving {LOG_ROOT}", flush=True)
    HTTPServer((HOST, PORT), Handler).serve_forever()
