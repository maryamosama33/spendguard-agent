"""Money the owner's rejections saved (F13).

- A rejected duplicate saved its whole amount: it would have been paid twice.
- A rejected overpriced request saved only the overcharge: the part above
  the usual price (18,000 at +20% saved 3,000, not 18,000).
"""

from spendguard.learning import DEFAULT_THRESHOLD_PCT, rejected_for_price
from spendguard.models import Expense


def overcharge(expense: Expense) -> float:
    """The part of the amount above the usual price."""
    deviation = expense.price_deviation_pct or 0.0
    if expense.amount is None or deviation <= 0:
        return 0.0
    return expense.amount - expense.amount / (1 + deviation / 100)


def _overpriced(expense: Expense) -> bool:
    deviation = expense.price_deviation_pct or 0.0
    return deviation > 0 and (rejected_for_price(expense) or deviation > DEFAULT_THRESHOLD_PCT)


def savings_summary(expenses: list[Expense], month: str) -> dict:
    """Savings from rejections decided in `month` ("YYYY-MM")."""
    rejected = [e for e in expenses if e.status == "rejected" and (e.decided_at or "").startswith(month)]
    duplicates = [e for e in rejected if e.duplicate_of is not None]
    overpriced = [e for e in rejected if e.duplicate_of is None and _overpriced(e)]
    duplicates_amount = round(sum(e.amount or 0.0 for e in duplicates), 2)
    overcharge_amount = round(sum(overcharge(e) for e in overpriced), 2)
    return {
        "month": month,
        "duplicates": {"count": len(duplicates), "amount": duplicates_amount},
        "overpricing": {"count": len(overpriced), "amount": overcharge_amount},
        "total_saved": round(duplicates_amount + overcharge_amount, 2),
    }
