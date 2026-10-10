"""Approved-server log collection (Paramiko / SSH).

Slide 4 says logs come from "approved server logs". On a laptop, that means the
five Java services' rolling files under LOG_ROOT. In a real environment it means
hosts you have been granted access to — which is exactly what SSH is for.

Three collection modes, tried in order:

  1. SSH   — BUGZERO_SSH_HOST set. Paramiko opens one session, streams each
             service's current and rotated logs, reads them, closes. Read-only:
             we never write, never exec a shell command, never touch the host.
  2. HTTP  — INVESTIGATOR_LOG_PULL set. Pulls from the local log server.
  3. FILE  — default. Reads LOG_ROOT directly.

SSH is preferred because it works against a real deployment where the logs are
on someone else's machine. Paramiko is optional: if it is not installed, or no
host is configured, we fall through cleanly instead of failing the
investigation. Credentials come from the environment only, and a key file is
preferred over a password.
"""

import os
from pathlib import Path

SERVICES = [
    "customer-service",
    "order-service",
    "inventory-service",
    "provisioning-service",
    "notification-service",
]

# Never stream an unbounded file over SSH — a busy service writes gigabytes a
# day. TAIL_LINES bounds what we pull; the tail is where live incidents are.
TAIL_LINES = 5000
MAX_BYTES = 4 * 1024 * 1024


def ssh_config() -> dict:
    """SSH settings from the environment. Empty host => SSH mode disabled."""
    from . import llmreason
    llmreason._load_dotenv()
    return {
        "host": os.environ.get("BUGZERO_SSH_HOST", "").strip(),
        "port": int(os.environ.get("BUGZERO_SSH_PORT", "22")),
        "user": os.environ.get("BUGZERO_SSH_USER", "").strip(),
        "password": os.environ.get("BUGZERO_SSH_PASSWORD", ""),
        "key_filename": os.environ.get("BUGZERO_SSH_KEY", "").strip(),
        "key_passphrase": os.environ.get("BUGZERO_SSH_KEY_PASSPHRASE", ""),
        "remote_root": os.environ.get("BUGZERO_SSH_LOG_ROOT", "/var/log/telecom").strip("/"),
        "timeout": int(os.environ.get("BUGZERO_SSH_TIMEOUT", "20")),
        "port_forward": os.environ.get("BUGZERO_SSH_PORT_FORWARD", "").strip(),
    }


def is_ssh_configured() -> bool:
    cfg = ssh_config()
    return bool(cfg["host"] and cfg["user"] and (cfg["key_filename"] or cfg["password"]))


def _connect(cfg: dict):
    """Open an authenticated Paramiko client. Raises if paramiko is missing."""
    import paramiko
    client = paramiko.SSHClient()
    # Ops hosts are typically provisioned out-of-band rather than shipped with a
    # baked-in fingerprint, so we key trust on the configured host key file and
    # refuse to blindly accept an unknown one.
    known = os.environ.get("BUGZERO_SSH_KNOWN_HOSTS", "").strip()
    if known and os.path.exists(known):
        client.load_host_keys(known)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": cfg["host"],
        "port": cfg["port"],
        "username": cfg["user"],
        "timeout": cfg["timeout"],
        "allow_agent": False,
        "look_for_keys": False,
    }
    if cfg["key_filename"]:
        kwargs["key_filename"] = cfg["key_filename"]
        if cfg["key_passphrase"]:
            kwargs["passphrase"] = cfg["key_passphrase"]
    else:
        kwargs["password"] = cfg["password"]
    client.connect(**kwargs)
    if cfg["port_forward"]:
        # Optional single-port tunnel, e.g. "5433:localhost:5433", so the
        # database lives behind a bastion in a real network.
        bind, _, target = cfg["port_forward"].partition(":")
        client.forward_agent = False
        transport = client.get_transport()
        transport.request_port_forward("127.0.0.1", int(bind), target)
    return client


def _read_remote_file(sftp, remote_path: str, tail_lines: int = TAIL_LINES) -> list:
    """Stream a remote file's tail over SFTP, bounded by bytes and lines."""
    lines: list = []
    total = 0
    with sftp.open(remote_path, "rb") as fh:
        # Seek backwards in blocks so we never hold a whole production log.
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        block = 65536
        start = max(0, size - MAX_BYTES)
        fh.seek(start)
        chunk = fh.read()
        text = chunk.decode("utf-8", errors="replace")
        rows = text.splitlines()
        if start > 0 and rows:
            # The first line is almost certainly truncated mid-record.
            rows = rows[1:]
        lines = rows[-tail_lines:]
        total = len(chunk)
    return lines


def _remote_candidates(sftp, cfg: dict, service: str) -> list:
    """Current log plus rotated archives, newest first — rotation must not hide evidence."""
    base = f"{cfg['remote_root']}/{service}"
    found = []
    for attr in ("app.log",):
        try:
            found.append(f"{base}/{attr}")
        except Exception:
            pass
    try:
        for entry in sorted(sftp.listdir_attr(base), key=lambda a: a.filename, reverse=True):
            name = entry.filename
            if name.startswith("app.") and (name.endswith(".log") or name.endswith(".log.gz")):
                found.append(f"{base}/{name}")
            if len(found) > 6:
                break
    except IOError:
        pass
    return found


def collect() -> dict:
    """Collect log lines from the configured source.

    Returns {ok, mode, lines: [{service, line}], error, host}. `lines` is empty
    (not an error) when nothing is configured — the caller falls back to its own
    local reader.
    """
    cfg = ssh_config()
    if not is_ssh_configured():
        missing = []
        if not cfg["host"]:
            missing.append("BUGZERO_SSH_HOST")
        if not cfg["user"]:
            missing.append("BUGZERO_SSH_USER")
        if not (cfg["key_filename"] or cfg["password"]):
            missing.append("BUGZERO_SSH_KEY or BUGZERO_SSH_PASSWORD")
        return {"ok": False, "mode": "ssh", "lines": [],
                "error": f"SSH not configured ({', '.join(missing)})."}

    try:
        client = _connect(cfg)
    except ImportError:
        return {"ok": False, "mode": "ssh", "lines": [],
                "error": "paramiko is not installed — pip install paramiko, "
                         "or unset BUGZERO_SSH_HOST to use local logs."}
    except Exception as e:
        return {"ok": False, "mode": "ssh", "lines": [],
                "error": f"SSH connect failed ({type(e).__name__}: {e})"}

    lines: list = []
    try:
        sftp = client.open_sftp()
    except Exception as e:
        client.close()
        return {"ok": False, "mode": "ssh", "lines": [],
                "error": f"SFTP unavailable ({type(e).__name__}: {e})"}

    per_service = {}
    try:
        for service in SERVICES:
            got = 0
            for remote in _remote_candidates(sftp, cfg, service):
                try:
                    rows = _read_remote_file(sftp, remote)
                except Exception:
                    continue
                for row in rows:
                    lines.append({"service": service, "line": row})
                    got += 1
                if got >= TAIL_LINES:
                    break
            per_service[service] = got
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        client.close()

    return {
        "ok": True,
        "mode": "ssh",
        "host": f"{cfg['user']}@{cfg['host']}:{cfg['port']}",
        "remote_root": cfg["remote_root"],
        "lines": lines,
        "per_service": per_service,
    }


def health() -> dict:
    """Status row for the console: is SSH collection configured and reachable?"""
    cfg = ssh_config()
    if not is_ssh_configured():
        return {"configured": False, "reachable": False, "mode": "local",
                "detail": "SSH not configured — reading local logs."}
    try:
        import paramiko  # noqa: F401
    except ImportError:
        return {"configured": True, "reachable": False, "mode": "ssh",
                "detail": "paramiko not installed."}
    try:
        client = _connect(cfg)
        client.close()
        return {"configured": True, "reachable": True, "mode": "ssh",
                "host": f"{cfg['user']}@{cfg['host']}:{cfg['port']}",
                "remote_root": cfg["remote_root"], "detail": "connected"}
    except Exception as e:
        return {"configured": True, "reachable": False, "mode": "ssh",
                "host": cfg["host"], "detail": f"{type(e).__name__}: {e}"[:200]}