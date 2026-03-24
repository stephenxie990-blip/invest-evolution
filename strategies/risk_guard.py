"""Risk guard gene.

This file is intentionally simple and editable.
Commander only parses metadata by default.
"""

GENE_META = {
    "id": "risk_guard",
    "name": "Risk Guard Gene",
    "enabled": True,
    "priority": 95,
    "description": "Portfolio level drawdown and exposure guardrails.",
}

def suggest_risk_overrides(context: dict) -> dict:
    """Optional helper function if you want Python-based custom logic."""
    drawdown = float(context.get("drawdown", 0.0))
    if drawdown > 0.10:
        return {"position_size": 0.10, "max_positions": 2}
    return {"position_size": 0.20, "max_positions": 5}
