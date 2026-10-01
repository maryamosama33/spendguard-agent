from spendguard.models import Expense


def _same_invoice(a: Expense, b: Expense) -> bool:
    return bool(a.invoice_number) and a.invoice_number == b.invoice_number


def _same_supplier_amount_date(a: Expense, b: Expense) -> bool:
    return (
        bool(a.supplier)
        and a.supplier == b.supplier
        and a.amount == b.amount
        and a.date == b.date
    )


def find_duplicate(candidate: Expense, existing: list[Expense]) -> Expense | None:
    """Return the first existing expense that looks like a repeat of candidate."""
    for other in existing:
        if _same_invoice(candidate, other) or _same_supplier_amount_date(candidate, other):
            return other
    return None


PRICE_ANOMALY_THRESHOLD = 0.15
RECENT_PURCHASES_WINDOW = 3


def _same_supplier_item(a: Expense, b: Expense) -> bool:
    return (
        bool(a.supplier)
        and bool(a.item)
        and a.supplier == b.supplier
        and a.item.strip().lower() == b.item.strip().lower()
    )


def _matching_history(candidate: Expense, history: list[Expense]) -> list[Expense]:
    return [e for e in history if e.amount is not None and _same_supplier_item(candidate, e)]


def _recent_average(records: list[Expense], window: int) -> float:
    recent = sorted(records, key=lambda e: e.date or "", reverse=True)[:window]
    return sum(e.amount for e in recent) / len(recent)


def check_price_anomaly(
    candidate: Expense,
    history: list[Expense],
    threshold: float = PRICE_ANOMALY_THRESHOLD,
    window: int = RECENT_PURCHASES_WINDOW,
) -> dict:
    """Flag if candidate's price is abnormally high vs. recent same-supplier-item history."""
    matches = _matching_history(candidate, history)
    if not matches or candidate.amount is None:
        return {
            "is_anomaly": False,
            "average_price": None,
            "deviation_pct": None,
            "compared_count": len(matches),
        }

    average = _recent_average(matches, window)
    deviation_pct = (candidate.amount - average) / average if average else 0.0

    return {
        "is_anomaly": deviation_pct > threshold,
        "average_price": round(average, 2),
        "deviation_pct": round(deviation_pct * 100, 1),
        "compared_count": len(matches),
    }
