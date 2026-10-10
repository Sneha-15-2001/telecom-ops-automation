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
    """Return {file, name, start_line, end_line, source} for a method, or None."""
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
    return {"file": rel, "name": method, "start_line": start + 1, "end_line": end + 1,
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


def relevant(ticket_text: str, services=None, limit: int = 6) -> list:
    """Method BODIES for the methods a ticket plausibly points at.

    catalog() gives the model names only, so it guesses: asked about a payment
    that was "recorded but not validated" it picks record() and proposes marking
    the payment VALIDATED at write time — wrong method, and a fix that would
    mark payments validated before any check ran.

    Showing the actual source of the few methods the ticket's words overlap
    gives the model enough to pick the right one. Matching is by token overlap
    with the ticket plus the domain nouns, and the body is truncated, so this
    stays cheap.
    """
    if not REPO_ROOT.exists():
        return []
    cat = catalog()
    if not cat:
        return []

    # Tokens worth matching on: domain nouns plus anything substantial from the
    # ticket itself. Common English words are dropped so "the" never matches.
    stop = {"the", "and", "for", "with", "not", "but", "has", "was", "are", "this",
            "that", "from", "into", "when", "then", "they", "have", "order", "incident",
            "ticket", "issue", "error", "customer", "please", "check", "should", "would",
            "never", "always", "still", "after", "before", "while", "being", "each"}
    words = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", ticket_text or ""):
        low = w.lower()
        # Identifiers like ORD-F6CCF2B1 are noise: they substring-match real
        # words ("ord" inside "record") and would send the model to the wrong method.
        if len(low) < 4 or any(ch.isdigit() for ch in low) or low in stop:
            continue
        words.add(low)
    # Domain nouns the ticket may not spell out but the failure is about.
    words |= {"payment", "validate", "reserve", "activate", "notify", "cancel",
              "rollback", "fulfil", "provision", "hold", "status", "state"}

    scored = []
    for key, methods in cat.items():
        service, _, classname = key.partition(":")
        if services and not any(s.split("-")[0] in service for s in services):
            continue
        cl = classname.lower()
        # A class named for the domain noun is a stronger signal than a method
        # whose name happens to contain one — "PaymentService" beats "record".
        class_score = sum(2 for w in words if w in cl)
        if not class_score:
            continue
        # Controllers and clients are thin adapters that delegate; the defect is
        # essentially never there. Bias hard toward the service layer.
        adapter = classname.endswith("Controller") or classname.endswith("Client")
        # Entities are pure data holders — every method is a getter or setter, so
        # a fix never belongs in one. They otherwise crowd the top on their
        # class-name match alone.
        entity = methods and all(
            m.startswith(("get", "set", "is", "has", "to")) for m in methods)
        for method in methods:
            low = method.lower()
            score = class_score + sum(1 for w in words if w in low)
            if adapter:
                score = score - 4   # sink, but do not erase outright
            if entity:
                score = score - 6
            if score > 0:
                scored.append((score, service, classname, method))
    scored.sort(key=lambda x: (-x[0], x[3]))
    if not scored:
        return []

    out = []
    seen = set()
    per_class = {}
    for score, service, classname, method in scored:
        # Cap per class so one large class cannot crowd out the others.
        if per_class.get(classname, 0) >= 2:
            continue
        if (classname, method) in seen:
            continue
        seen.add((classname, method))
        hit = get_method(service, classname, method)
        if hit:
            out.append({
                "service": service,
                "class_name": classname,
                "method": method,
                "file": hit["file"],
                "start_line": hit["start_line"],
                "source": hit["source"][:1800],
            })
            per_class[classname] = per_class.get(classname, 0) + 1
        if len(out) >= limit:
            break
    return out


def catalog() -> dict:
    """Live class -> public-method catalog of the Java backend (for prompts).

    Gives the model only REAL method names to choose from, killing invented
    methods like queueNotification. Computed from disk on every call."""
    result = {}
    for service, dirname in (("customer-service", "customer-service"),
                             ("order-service", "order-service"),
                             ("inventory-service", "inventory-service"),
                             ("provisioning-service", "provisioning-service"),
                             ("notification-service", "notification-service")):
        root = REPO_ROOT / dirname
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.java")):
            if "/test/" in str(path):
                continue
            try:
                src = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            cls = re.search(r"\bclass\s+(\w+)", src)
            if not cls:
                continue
            methods = re.findall(r"public\s+[\w<>\[\],\s]+\s+(\w+)\s*\(", src)
            methods = [m for m in dict.fromkeys(methods) if m != cls.group(1)]
            if methods:
                result[f"{service}:{cls.group(1)}"] = methods
    return result
