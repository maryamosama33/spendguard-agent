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


def test_invoice_number_matches_ignoring_case_spaces_dashes_and_arabic_digits():
    existing = [_expense(invoice_number="SC-1140")]

    for typed in ["SC 1140", "sc-1140", "SC1140", "SC-١١٤٠"]:
        assert find_duplicate(_expense(invoice_number=typed, amount=1.0, date="2000-01-01"), existing)


def test_same_invoice_number_from_another_supplier_is_not_a_duplicate():
    candidate = _expense(invoice_number="1001", supplier="Other Co", amount=5.0, date="2026-01-01")

    assert find_duplicate(candidate, [_expense(invoice_number="1001")]) is None


def test_same_invoice_number_matches_when_supplier_unknown():
    candidate = _expense(invoice_number="INV-001", supplier=None)

    assert find_duplicate(candidate, [_expense(invoice_number="INV-001")]) is not None


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


def test_saved_expense_does_not_match_its_own_row():
    candidate = _expense(id=37)
    existing = [_expense(id=37)]

    assert find_duplicate(candidate, existing) is None


def test_saved_expense_still_matches_a_different_row():
    candidate = _expense(id=37)
    existing = [_expense(id=37), _expense(id=6)]

    assert find_duplicate(candidate, existing).id == 6


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


def _tons(amount: float, quantity: float, date: str = "2026-09-30", unit: str = "ton") -> Expense:
    return _expense(amount=amount, quantity=quantity, unit=unit, date=date, item="steel")


STEEL_HISTORY = [_tons(15000.0, 1, "2026-09-01"), _tons(30000.0, 2, "2026-09-10"), _tons(15000.0, 1, "2026-09-20")]


def test_bigger_order_at_same_unit_price_is_not_anomalous():
    result = check_price_anomaly(_tons(45000.0, 3), STEEL_HISTORY)

    assert result["is_anomaly"] is False
    assert (result["basis"], result["unit"], result["average_price"]) == ("unit_price", "ton", 15000.0)


def test_small_overpriced_order_is_caught_per_unit():
    # Half a ton for 9,000: a smaller total than any past invoice, but 20% more per ton.
    result = check_price_anomaly(_tons(9000.0, 0.5), STEEL_HISTORY)

    assert result["is_anomaly"] is True
    assert result["deviation_pct"] == 20.0


def test_without_quantity_falls_back_to_totals():
    result = check_price_anomaly(_expense(amount=18000.0, item="steel"), STEEL_HISTORY)

    assert result["basis"] == "total"
    assert result["average_price"] == 20000.0


def test_different_unit_falls_back_to_totals():
    result = check_price_anomaly(_tons(18000.0, 18000, unit="kg"), STEEL_HISTORY)

    assert result["basis"] == "total"


def test_price_anomaly_no_history_is_not_anomalous():
    candidate = _expense(amount=5000.0, item="steel")

    result = check_price_anomaly(candidate, [])

    assert result["is_anomaly"] is False
    assert result["average_price"] is None
