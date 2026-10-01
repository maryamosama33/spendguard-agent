from spendguard.models import Expense
from spendguard.query import filter_expenses, summarize


def _expense(**overrides) -> Expense:
    base = dict(
        project="Site A",
        supplier="Al-Nasr",
        cost_item="materials",
        item="steel",
        date="2026-09-15",
        amount=1000.0,
    )
    base.update(overrides)
    return Expense(**base)


def test_filter_by_project():
    expenses = [_expense(project="Site A"), _expense(project="Site B")]

    result = filter_expenses(expenses, project="Site A")

    assert len(result) == 1
    assert result[0].project == "Site A"


def test_filter_by_date_range():
    expenses = [_expense(date="2026-09-01"), _expense(date="2026-10-01")]

    result = filter_expenses(expenses, date_from="2026-09-01", date_to="2026-09-30")

    assert len(result) == 1
    assert result[0].date == "2026-09-01"


def test_filter_is_case_insensitive():
    expenses = [_expense(supplier="Al-Nasr")]

    result = filter_expenses(expenses, supplier="al-nasr")

    assert len(result) == 1


def test_filter_no_filters_returns_all():
    expenses = [_expense(), _expense()]

    assert filter_expenses(expenses) == expenses


def test_filter_combines_multiple_criteria():
    expenses = [
        _expense(project="Site A", cost_item="materials"),
        _expense(project="Site A", cost_item="transport"),
        _expense(project="Site B", cost_item="materials"),
    ]

    result = filter_expenses(expenses, project="Site A", cost_item="materials")

    assert len(result) == 1


def test_summarize_totals_and_breakdown():
    expenses = [
        _expense(cost_item="materials", amount=1000.0),
        _expense(cost_item="materials", amount=500.0),
        _expense(cost_item="transport", amount=300.0),
    ]

    summary = summarize(expenses)

    assert summary["total_amount"] == 1800.0
    assert summary["count"] == 3
    assert summary["by_cost_item"] == {"materials": 1500.0, "transport": 300.0}


def test_summarize_empty_list():
    summary = summarize([])

    assert summary["total_amount"] == 0
    assert summary["count"] == 0
    assert summary["by_cost_item"] == {}


def test_summarize_ignores_expenses_with_missing_amount():
    expenses = [_expense(amount=None), _expense(amount=200.0)]

    summary = summarize(expenses)

    assert summary["total_amount"] == 200.0
    assert summary["count"] == 2
