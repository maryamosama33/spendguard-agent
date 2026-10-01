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
