from spendguard.checks import check_price_anomaly, find_duplicate
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


def _history_entry(amount: float, date: str, item: str = "steel", supplier: str = "Al-Nasr Sand Co") -> Expense:
    return _expense(amount=amount, date=date, item=item, supplier=supplier)


def test_price_anomaly_flagged_when_above_threshold():
    candidate = _expense(amount=1200.0, item="steel", date="2026-09-30")
    history = [
        _history_entry(1000.0, "2026-09-01"),
        _history_entry(1000.0, "2026-09-10"),
        _history_entry(1000.0, "2026-09-20"),
    ]

    result = check_price_anomaly(candidate, history)

    assert result["is_anomaly"] is True
    assert result["average_price"] == 1000.0
    assert result["compared_count"] == 3


def test_price_not_anomalous_within_threshold():
    candidate = _expense(amount=1050.0, item="steel", date="2026-09-30")
    history = [_history_entry(1000.0, "2026-09-01")]

    result = check_price_anomaly(candidate, history)

    assert result["is_anomaly"] is False


def test_price_anomaly_only_uses_recent_window():
    candidate = _expense(amount=1100.0, item="steel", date="2026-09-30")
    history = [
        _history_entry(2000.0, "2026-01-01"),  # old, outside window of 3
        _history_entry(1000.0, "2026-09-01"),
        _history_entry(1000.0, "2026-09-10"),
        _history_entry(1000.0, "2026-09-20"),
    ]

    result = check_price_anomaly(candidate, history)

    assert result["average_price"] == 1000.0


def test_price_anomaly_ignores_different_item_or_supplier():
    candidate = _expense(amount=5000.0, item="steel", supplier="Al-Nasr Sand Co")
    history = [
        _history_entry(100.0, "2026-09-01", item="cement"),
        _history_entry(100.0, "2026-09-01", supplier="Other Co"),
    ]

    result = check_price_anomaly(candidate, history)

    assert result["compared_count"] == 0
    assert result["is_anomaly"] is False


def test_price_anomaly_no_history_is_not_anomalous():
    candidate = _expense(amount=5000.0, item="steel")

    result = check_price_anomaly(candidate, [])

    assert result["is_anomaly"] is False
    assert result["average_price"] is None
