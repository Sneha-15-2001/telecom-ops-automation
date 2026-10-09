"""Source-code locator (grounds code fixes in real files + line numbers).

Given a service + area string like "ReservationService.requireActive / confirm",
finds the repo-relative file, and extracts the named methods with exact line
ranges via brace matching. No LLM involved — pure source reading, so the
numbers are always truthful. Returns None when the code isn't checked out
(e.g. inside the automation-only repo).
"""

import os
import re
from pathlib import Path

REPO_ROOT = Path(os.environ.get(
    "PRODUCT_REPO_ROOT",
    Path(__file__).resolve().parents[3],
))

SERVICE_DIR = {
    "customer-service": "customer-service",
    "order-service": "order-service",
    "inventory-service": "inventory-service",
    "provisioning-service": "provisioning-service",
    "notification-service": "notification-service",
    "customer": "customer-service",
    "order": "order-service",
    "inventory": "inventory-service",
    "provisioning": "provisioning-service",
    "notification": "notification-service",
}


def _service_dir(service: str):
    first = (service or "").split("+")[0].split("/")[0].strip().lower().replace(" ", "-")
    for key, val in SERVICE_DIR.items():
        if key in first or first in key:
            return REPO_ROOT / val
    return None


def find_class(service: str, classname: str):
    """Return repo-relative path + total lines, or None."""
    root = _service_dir(service)
    if not root or not root.exists():
        return None
    matches = list(root.rglob(f"{classname}.java"))
    if not matches:
        return None
    path = matches[0]
    try:
        total = sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return {"file": str(path.relative_to(REPO_ROOT)), "lines": total}


def get_method(service: str, classname: str, method: str):
    """Return {file, start_line, end_line, source} for a method, or None."""
    root = _service_dir(service)
    if not root or not root.exists():
        return None
    matches = list(root.rglob(f"{classname}.java"))
    if not matches:
        return None
    path = matches[0]
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    lines = src.split("\n")
    pattern = re.compile(rf"^[\s\w<>\[\],@]*\b{re.escape(method)}\s*\(")
    start = None
    for i, line in enumerate(lines):
        if pattern.search(line):
            start = i
            break
    if start is None:
        return None
    depth = 0
    opened = False
    end = None
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if "{" in lines[i]:
            opened = True
        if opened and depth == 0:
            end = i
            break
    if end is None:
        end = min(start + 40, len(lines) - 1)
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    return {"file": rel, "start_line": start + 1, "end_line": end + 1,
            "source": "\n".join(lines[start:end + 1])}


def locate(service: str, area: str):
    """Parse 'ClassName.method / other' and locate each method. Returns
    {class_file, methods: [...]} with Nones where unresolvable."""
    tokens = re.findall(r"[A-Za-z_]\w*", area or "")
    classname = next((t for t in tokens if t[0].isupper()), None)
    methods = [t for t in tokens if not t[0].isupper() and t not in ("java", "lang")]
    if not classname:
        return {"class_file": None, "methods": []}
    found = find_class(service, classname)
    out = {"class_file": found, "methods": []}
    seen = set()
    for m in methods:
        if m in seen:
            continue
        seen.add(m)
        hit = get_method(service, classname, m)
        if hit:
            out["methods"].append(hit)
    return out
