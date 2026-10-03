import re
from collections.abc import Callable

from spendguard.models import Expense


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def invoice_key(number: str | None) -> str:
    """"SC 1140", "sc-1140" and "SC-١١٤٠" are the same invoice: SC1140."""
    return re.sub(r"[\W_]", "", (number or "").translate(_DIGITS)).upper()


def _compatible_suppliers(a: Expense, b: Expense) -> bool:
    """Two suppliers can both issue invoice "1001"; only a known, different
    supplier rules a match out."""
    return not (a.supplier and b.supplier) or a.supplier == b.supplier


def _same_invoice(a: Expense, b: Expense) -> bool:
    key = invoice_key(a.invoice_number)
    return bool(key) and key == invoice_key(b.invoice_number) and _compatible_suppliers(a, b)


def _same_supplier_amount_date(a: Expense, b: Expense) -> bool:
    return (
        bool(a.supplier)
        and a.supplier == b.supplier
        and a.amount == b.amount
        and a.date == b.date
    )


def _is_same_record(a: Expense, b: Expense) -> bool:
    return a.id is not None and a.id == b.id


def find_duplicate(candidate: Expense, existing: list[Expense]) -> Expense | None:
    """Return the first existing expense that looks like a repeat of candidate.
    An already-saved candidate never matches its own row."""
    for other in existing:
        if _is_same_record(candidate, other):
            continue
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


def unit_price(expense: Expense) -> float | None:
    if expense.amount is None or not expense.quantity:
        return None
    return expense.amount / expense.quantity


def _same_unit(a: Expense, b: Expense) -> bool:
    return (a.unit or "").strip().lower() == (b.unit or "").strip().lower()


def _comparable(candidate: Expense, matches: list[Expense]) -> tuple[str, Callable[[Expense], float], list[Expense]]:
    """Compare price per unit when both sides have a quantity in the same unit
    (2 tons is not "100% more expensive" than 1 ton); else the invoice totals."""
    if unit_price(candidate) is not None:
        per_unit = [e for e in matches if unit_price(e) is not None and _same_unit(candidate, e)]
        if per_unit:
            return "unit_price", unit_price, per_unit
    return "total", lambda e: e.amount, matches


def _recent_average(records: list[Expense], price: Callable[[Expense], float], window: int) -> float:
    recent = sorted(records, key=lambda e: e.date or "", reverse=True)[:window]
    return sum(price(e) for e in recent) / len(recent)


def check_price_anomaly(
    candidate: Expense,
    history: list[Expense],
    threshold: float = PRICE_ANOMALY_THRESHOLD,
    window: int = RECENT_PURCHASES_WINDOW,
) -> dict:
    """Flag if candidate's price is abnormally high vs. recent same-supplier-item history.
    "basis" says what was compared: "unit_price" or "total"."""
    matches = _matching_history(candidate, history)
    if not matches or candidate.amount is None:
        return {
            "is_anomaly": False,
            "average_price": None,
            "deviation_pct": None,
            "compared_count": len(matches),
            "basis": None,
            "unit": None,
        }

    basis, price, compared = _comparable(candidate, matches)
    average = _recent_average(compared, price, window)
    deviation_pct = (price(candidate) - average) / average if average else 0.0

    return {
        "is_anomaly": deviation_pct > threshold,
        "average_price": round(average, 2),
        "deviation_pct": round(deviation_pct * 100, 1),
        "compared_count": len(compared),
        "basis": basis,
        "unit": candidate.unit if basis == "unit_price" else None,
    }
