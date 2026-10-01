from spendguard.models import Expense


def _matches(
    expense: Expense,
    *,
    project: str | None,
    supplier: str | None,
    cost_item: str | None,
    item: str | None,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    if project and (expense.project or "").lower() != project.lower():
        return False
    if supplier and (expense.supplier or "").lower() != supplier.lower():
        return False
    if cost_item and (expense.cost_item or "").lower() != cost_item.lower():
        return False
    if item and (expense.item or "").lower() != item.lower():
        return False
    if date_from and (expense.date or "") < date_from:
        return False
    if date_to and (expense.date or "") > date_to:
        return False
    return True


def filter_expenses(
    expenses: list[Expense],
    project: str | None = None,
    supplier: str | None = None,
    cost_item: str | None = None,
    item: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[Expense]:
    return [
        e
        for e in expenses
        if _matches(
            e,
            project=project,
            supplier=supplier,
            cost_item=cost_item,
            item=item,
            date_from=date_from,
            date_to=date_to,
        )
    ]


def _breakdown_by_cost_item(expenses: list[Expense]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for e in expenses:
        if e.amount is None:
            continue
        key = e.cost_item or "uncategorized"
        totals[key] = totals.get(key, 0.0) + e.amount
    return {k: round(v, 2) for k, v in totals.items()}


def summarize(expenses: list[Expense]) -> dict:
    total = sum(e.amount for e in expenses if e.amount is not None)
    return {
        "total_amount": round(total, 2),
        "count": len(expenses),
        "by_cost_item": _breakdown_by_cost_item(expenses),
    }
