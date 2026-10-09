"""Permanent code-fix recommender — LLM-generated (no canned fixes).

Shapes the LLM's code_fix analysis into change-proposal cards
(service/area/before/after/tests/risks/deploy). Without an LLM verdict there
is nothing to shape — the endpoint says so instead of inventing a diff.
"""

DISCLAIMER = ("PROPOSAL ONLY — human review, tests and deployment required. "
              "The investigator cannot commit or deploy code.")


def propose(inv: dict, incident_text: str = "") -> dict:
    llm = inv.get("llm") or {}
    analysis = llm.get("analysis") if llm.get("enabled") else None
    draft = (analysis or {}).get("code_fix") if analysis else None
    if not draft:
        return {"disclaimer": DISCLAIMER, "fixes": [],
                "message": ("No code fix drafted — enable the LLM and re-run; "
                            "nothing is proposed rather than something invented.")}
    return {
        "disclaimer": DISCLAIMER,
        "fixes": [{
            "title": f"Code fix in {draft.get('service', '?')}",
            "service": draft.get("service", "?"),
            "area": draft.get("area", "?"),
            "class_name": draft.get("class_name", ""),
            "method": draft.get("method", ""),
            "problem": analysis.get("root_cause", ""),
            "before": draft.get("before", ""),
            "after": draft.get("after", ""),
            "tests": ["Add a regression test replaying this incident's evidence",
                      "Re-run the affected service suite green"],
            "risks": "Review blast radius with the owning team before merging.",
            "deploy_notes": "Ship behind existing release process; watch the related metric.",
        }],
    }
