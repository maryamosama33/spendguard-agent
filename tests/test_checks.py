from spendguard.checks import find_duplicate
from spendguard.models import Expense


def _expense(**overrides) -> Expense:
    base = dict(
        date="2026-09-30",
        amount=3000.0,
        supplier="Al-Nasr Sand Co",
        project="Site A",
        invoice_number="INV-001",
    )
    base.update(overrides)
    return Expense(**base)


def test_matches_on_same_invoice_number():
    candidate = _expense(invoice_number="INV-001")
    existing = [_expense(invoice_number="INV-001", amount=9999.0, date="2000-01-01")]

    match = find_duplicate(candidate, existing)

    assert match is not None


def test_matches_on_supplier_amount_date_without_invoice_number():
    candidate = _expense(invoice_number=None)
    existing = [_expense(invoice_number=None)]

    match = find_duplicate(candidate, existing)

    assert match is not None


def test_no_match_when_amount_differs():
    candidate = _expense(invoice_number=None, amount=3000.0)
    existing = [_expense(invoice_number=None, amount=5000.0)]

    assert find_duplicate(candidate, existing) is None


def test_two_missing_invoice_numbers_do_not_false_match():
    candidate = _expense(invoice_number=None, supplier=None, amount=None, date=None)
    existing = [_expense(invoice_number=None, supplier=None, amount=None, date=None)]

    assert find_duplicate(candidate, existing) is None


def test_no_match_against_empty_history():
    candidate = _expense()

    assert find_duplicate(candidate, []) is None
