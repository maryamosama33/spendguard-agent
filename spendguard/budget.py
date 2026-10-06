"""Project budgets vs. actual spend (F18) and threshold alerts (F19).

Actual spend is approved expenses only. A request is flagged to the owner
when approving it would take its project to 80% of the budget or past it.
"""

import json
from pathlib import Path

from spendguard.models import Expense

BUDGETS_FILE = Path(__file__).resolve().parent.parent / "data" / "seed" / "budgets.json"
NEAR_LIMIT_PCT = 80.0


def load_budgets(path: Path | None = None) -> dict[str, float]:
    """Project name -> total budget (EGP); empty if no budgets file."""
    path = path or BUDGETS_FILE
    if not path.exists():
        return {}
    return {project: float(amount) for project, amount in json.loads(path.read_text(encoding="utf-8")).items()}


def project_spend(project: str, expenses: list[Expense]) -> float:
    """Approved spending on `project`."""
    return sum(e.amount or 0.0 for e in expenses if e.status == "approved" and e.project == project)


def budget_status(project: str, budget: float, spent: float) -> dict:
    return {
        "project": project,
        "budget": budget,
        "spent": round(spent, 2),
        "remaining": round(budget - spent, 2),
        "used_pct": round(spent / budget * 100, 1) if budget else None,
    }


def _alert_level(used_pct: float) -> str | None:
    if used_pct > 100:
        return "over"
    if used_pct >= NEAR_LIMIT_PCT:
        return "near"
    return None


def budget_alert(expense: Expense, budgets: dict[str, float], expenses: list[Expense]) -> dict | None:
    """The project's budget as it would be after approving `expense`, if that
    reaches the warning level; None otherwise (or if it has no budget)."""
    budget = budgets.get(expense.project or "")
    if not budget or expense.amount is None:
        return None
    after = budget_status(expense.project, budget, project_spend(expense.project, expenses) + expense.amount)
    level = _alert_level(after["used_pct"])
    return after | {"level": level} if level else None
