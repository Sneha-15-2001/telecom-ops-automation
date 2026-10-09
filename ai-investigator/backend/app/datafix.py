"""Temporary data-fix recommender — LLM-generated (no canned fixes).

Takes the LLM analysis attached to an investigation and shapes it into the
standard proposal cards (kind/sql/validation/rollback/approval). Without an
LLM verdict there is nothing to shape — the endpoint says so instead of
inventing SQL.
"""

DISCLAIMER = ("PROPOSAL ONLY — human approval required. The investigator cannot "
              "execute fixes.")


def propose(inv: dict) -> dict:
    llm = inv.get("llm") or {}
    analysis = llm.get("analysis") if llm.get("enabled") else None
    if not analysis or not analysis.get("data_fix_sql"):
        return {"disclaimer": DISCLAIMER, "fixes": [],
                "message": ("No data-fix drafted — enable the LLM and re-run; "
                            "nothing is proposed rather than something invented.")}
    return {
        "disclaimer": DISCLAIMER,
        "fixes": [{
            "kind": "sql",
            "title": f"Temporary data correction ({analysis.get('fix_type', 'DATA')})",
            "why": analysis.get("root_cause", ""),
            "sql": [analysis["data_fix_sql"]],
            "validation": analysis.get("data_fix_validation") or "Re-query the affected rows.",
            "rollback": analysis.get("data_fix_rollback") or "Reverse the UPDATE if validation fails.",
            "requires_approval": True,
        }],
    }
