"""Jira Cloud REST v3 client (BugZero handoff).

Reads a live issue, creates the permanent-fix ticket a developer works from,
comments follow-up evidence, and moves issues through the workflow. Transport is
stdlib urllib over the Atlassian REST v3 API — no extra dependency, and the same
code path works for Jira Cloud (https://<site>.atlassian.net) or a Jira Server
/DC instance behind JIRA_BASE_URL when you drop /rest/api/3.

Nothing here raises. Every function returns a dict that either carries a result
or an "ok": False plus a reason, so a misconfigured or unreachable Jira can never
take down an investigation that already produced good evidence.

READ vs WRITE is explicit: get_issue/list_issues/search are reads; create_issue,
add_comment and transition are writes and are ONLY ever called from handoff.py,
which builds them from evidence a human can review first.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# Atlassian document format. Jira Cloud defaults to v2 for the editor; leaving
# this unset means the body renders as plain wiki markup, which is what our
# evidence pack wants anyway.
ADF_VERSION = 1

# Jira caps most issue payloads well below this; we truncate defensively so a
# giant log excerpt can never produce a rejected request.
MAX_DESCRIPTION_CHARS = 18000
MAX_COMMENT_CHARS = 8000


def config() -> dict:
    """Jira settings from the environment. Empty base_url => disabled."""
    from . import llmreason
    llmreason._load_dotenv()  # same .env convention as the LLM layer
    base = os.environ.get("JIRA_BASE_URL", "").strip().rstrip("/")
    project = os.environ.get("JIRA_PROJECT_KEY", "").strip()
    return {
        "base_url": base,
        "email": os.environ.get("JIRA_EMAIL", "").strip(),
        "api_token": os.environ.get("JIRA_API_TOKEN", "").strip(),
        "project_key": project,
        "issue_type": os.environ.get("JIRA_ISSUE_TYPE", "Task").strip() or "Task",
        "timeout": int(os.environ.get("JIRA_TIMEOUT_SECONDS", "20")),
    }


def is_configured() -> bool:
    cfg = config()
    return bool(cfg["base_url"] and cfg["email"] and cfg["api_token"])


def _auth_header(cfg: dict) -> str:
    """Atlassian uses HTTP Basic with email:api_token for Cloud, not Bearer."""
    import base64
    raw = f"{cfg['email']}:{cfg['api_token']}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _api_base(cfg: dict) -> str:
    """Jira Cloud exposes /rest/api/3; Server/DC exposes /rest/api/2."""
    if os.environ.get("JIRA_API_VERSION", "3").strip() == "2":
        return cfg["base_url"] + "/rest/api/2"
    return cfg["base_url"] + "/rest/api/3"


def _request(cfg: dict, method: str, path: str, payload: dict = None,
             query: dict = None) -> dict:
    """One REST call. Returns {ok, status, data|error} and never raises."""
    if not is_configured():
        return {"ok": False, "status": 0,
                "error": "Jira not configured — set JIRA_BASE_URL, JIRA_EMAIL, "
                         "JIRA_API_TOKEN in ai-investigator/backend/.env"}
    url = _api_base(cfg) + path
    if query:
        clean = {k: v for k, v in query.items() if v not in (None, "")}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": _auth_header(cfg),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
            raw = resp.read().decode()
            data = json.loads(raw) if raw.strip() else {}
            return {"ok": True, "status": resp.status, "data": data}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        return {"ok": False, "status": e.code, "error": f"HTTP {e.code}: {detail}"}
    except Exception as e:
        return {"ok": False, "status": 0,
                "error": f"{type(e).__name__}: {e}"}


def _adf(text: str) -> dict:
    """Wrap plain/wiki text as an Atlassian Document Format paragraph block."""
    return {
        "type": "doc",
        "version": ADF_VERSION,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def _plaintext(value) -> str:
    """Jira Cloud can return a field as a plain string or as an ADF doc."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        chunks = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text" and isinstance(node.get("text"), str):
                    chunks.append(node["text"])
                for child in node.get("content", []) or []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return "".join(chunks)
    return ""


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def get_issue(issue_key: str, fields: str = None) -> dict:
    """Fetch one issue. `fields` is a comma-separated Jira field list."""
    cfg = config()
    if not issue_key:
        return {"ok": False, "status": 0, "error": "No issue key given."}
    res = _request(cfg, "GET", f"/issue/{urllib.parse.quote(issue_key)}",
                   query={"fields": fields or
                          "summary,status,priority,issuetype,description,"
                          "created,reporter,labels,comment,customfield_10000"})
    if not res["ok"]:
        return res
    issue = res["data"].get("fields", {}) or {}
    comments = []
    for c in issue.get("comment", {}).get("comments", []) or []:
        comments.append({
            "id": c.get("id"),
            "author": (c.get("author") or {}).get("displayName", ""),
            "created": c.get("created", ""),
            "body": _plaintext(c.get("body")),
        })
    return {
        "ok": True,
        "status": res["status"],
        "key": res["data"].get("key"),
        "summary": issue.get("summary", ""),
        "issue_type": (issue.get("issuetype") or {}).get("name", ""),
        "status": ((issue.get("status") or {}).get("name", "")),
        "status_category": ((issue.get("status") or {}).get("statusCategory") or {}).get("key", ""),
        "priority": (issue.get("priority") or {}).get("name", ""),
        "reporter": (issue.get("reporter") or {}).get("displayName", ""),
        "created": issue.get("created", ""),
        "labels": issue.get("labels", []) or [],
        "description": _plaintext(issue.get("description")),
        "comments": comments,
    }


def list_issues(project_key: str = None, jql: str = None, max_results: int = 25,
                status: str = None) -> dict:
    """List candidate issues for the console's ticket picker.

    Defaults to the configured project's open work, newest first, so an operator
    picks a real ticket instead of pasting free text.
    """
    cfg = config()
    project_key = project_key or cfg["project_key"]
    if not project_key and not jql:
        return {"ok": False, "status": 0,
                "error": "No JIRA_PROJECT_KEY configured."}
    if not jql:
        clauses = []
        if project_key:
            clauses.append(f"project = {project_key}")
        if status:
            clauses.append(f'status = "{status}"')
        jql = " ".join(clauses) + " ORDER BY created DESC"
    res = _request(cfg, "GET", "/search", query={
        "jql": jql,
        "maxResults": max(1, min(int(max_results), 100)),
        "fields": "summary,status,priority,created,reporter,labels",
    })
    if not res["ok"]:
        return res
    issues = []
    for it in res["data"].get("issues", []) or []:
        f = it.get("fields", {}) or {}
        issues.append({
            "key": it.get("key"),
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "reporter": (f.get("reporter") or {}).get("displayName", ""),
            "created": f.get("created", ""),
            "labels": f.get("labels", []) or [],
        })
    return {"ok": True, "status": res["status"], "count": len(issues), "issues": issues}


# --------------------------------------------------------------------------
# Writes — called only by handoff.py, always from reviewed evidence
# --------------------------------------------------------------------------

def create_issue(summary: str, description: str, issue_type: str = None,
                 project_key: str = None, labels: list = None,
                 priority: str = None) -> dict:
    """Create the permanent-fix ticket BugZero hands to a developer."""
    cfg = config()
    project_key = project_key or cfg["project_key"]
    if not project_key:
        return {"ok": False, "status": 0, "error": "No JIRA_PROJECT_KEY configured."}
    desc = (description or "")[:MAX_DESCRIPTION_CHARS]
    fields = {
        "project": {"key": project_key},
        "summary": (summary or "BugZero: incident requiring a code fix")[:255],
        "description": _adf(desc),
        "issuetype": {"name": issue_type or cfg["issue_type"]},
    }
    clean_labels = [l for l in (labels or []) if l][:20]
    if clean_labels:
        fields["labels"] = clean_labels
    res = _request(cfg, "POST", "/issue", payload={"fields": fields})
    if not res["ok"]:
        return res
    return {"ok": True, "status": res["status"],
            "key": res["data"].get("key"),
            "url": _issue_url(cfg, res["data"].get("key"))}


def add_comment(issue_key: str, body: str) -> dict:
    """Post follow-up evidence to an existing issue (e.g. after a re-run)."""
    res = _request(config(), "POST",
                   f"/issue/{urllib.parse.quote(issue_key)}/comment",
                   payload={"body": _adf((body or "")[:MAX_COMMENT_CHARS])})
    if not res["ok"]:
        return res
    return {"ok": True, "status": res["status"], "id": res["data"].get("id")}


def transition(issue_key: str, transition_name: str) -> dict:
    """Move an issue through the workflow by transition NAME, not by id.

    Ids differ per Jira instance, so we look the name up first. A wrong name
    degrades to a clear message rather than a silent no-op.
    """
    cfg = config()
    look = _request(cfg, "GET", f"/issue/{urllib.parse.quote(issue_key)}/transitions")
    if not look["ok"]:
        return look
    available = [(t.get("id"), t.get("name"))
                 for t in look["data"].get("transitions", []) or []]
    match = next((tid for tid, name in available
                  if (name or "").lower() == transition_name.lower()), None)
    if not match:
        return {"ok": False, "status": look["status"],
                "error": f"No transition named {transition_name!r}. "
                          f"Available: {', '.join(n for _, n in available) or 'none'}",
                "available": [n for _, n in available]}
    res = _request(cfg, "POST",
                   f"/issue/{urllib.parse.quote(issue_key)}/transitions",
                   payload={"transition": {"id": match}})
    if not res["ok"]:
        return res
    return {"ok": True, "status": res["status"],
            "transition": transition_name, "available": [n for _, n in available]}


def link_issues(source_key: str, target_key: str, link_type: str = "Relates") -> dict:
    """Create an issue link so the source incident and the fix ticket stay joined."""
    cfg = config()
    look = _request(cfg, "GET", "/issueLinkType")
    if not look["ok"]:
        return look
    types = look["data"].get("issueLinkTypes", []) or []
    chosen = next((t for t in types if (t.get("name") or "").lower() == link_type.lower()),
                  types[0] if types else None)
    if not chosen:
        return {"ok": False, "status": 0, "error": "Jira exposes no issue link types."}
    payload = {
        "type": {"name": chosen["name"], "inward": chosen.get("inward", "relates to")},
        "inwardIssue": {"key": target_key},
        "outwardIssue": {"key": source_key},
    }
    res = _request(cfg, "POST", "/issueLink", payload=payload)
    if not res["ok"]:
        return res
    return {"ok": True, "status": res["status"], "link_type": chosen["name"]}


def _issue_url(cfg: dict, key: str) -> str:
    return f"{cfg['base_url']}/browse/{key}" if key else ""


def health() -> dict:
    """Report Jira reachability for the console's status row."""
    cfg = config()
    if not is_configured():
        return {"configured": False, "reachable": False,
                "detail": "Jira not configured (JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN)."}
    res = _request(cfg, "GET", "/myself", query={"fields": "displayName,emailAddress"})
    if not res["ok"]:
        return {"configured": True, "reachable": False,
                "base_url": cfg["base_url"], "detail": res["error"]}
    me = res["data"] or {}
    return {"configured": True, "reachable": True, "base_url": cfg["base_url"],
            "project_key": cfg["project_key"],
            "account": me.get("displayName", me.get("emailAddress", "")),
            "detail": "connected"}