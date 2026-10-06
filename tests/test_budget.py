import json

from spendguard.budget import budget_alert, budget_status, load_budgets, project_spend
from spendguard.messages import budget_message, owner_approval_request
from spendguard.models import Expense

BUDGETS = {"فيلات التجمع الخامس": 75000.0}


def _spent(amount: float, project: str = "فيلات التجمع الخامس", status: str = "approved") -> Expense:
    return Expense(amount=amount, project=project, status=status)


HISTORY = [_spent(40000.0), _spent(7470.0), _spent(9000.0, status="pending"),
           _spent(5000.0, status="rejected"), _spent(30000.0, project="مستودع 6 أكتوبر")]


def test_project_spend_counts_only_approved_expenses_of_that_project():
    assert project_spend("فيلات التجمع الخامس", HISTORY) == 47470.0


def test_budget_status_shows_remaining_and_percent_used():
    assert budget_status("فيلات التجمع الخامس", 75000.0, 47470.0) == {
        "project": "فيلات التجمع الخامس", "budget": 75000.0, "spent": 47470.0,
        "remaining": 27530.0, "used_pct": 63.3}


def test_no_alert_below_80_percent():
    assert budget_alert(_spent(5000.0, status="pending"), BUDGETS, HISTORY) is None


def test_alert_when_request_reaches_80_percent():
    alert = budget_alert(_spent(18000.0, status="pending"), BUDGETS, HISTORY)

    assert alert["level"] == "near"
    assert alert["used_pct"] == 87.3


def test_alert_when_request_goes_over_budget():
    alert = budget_alert(_spent(30000.0, status="pending"), BUDGETS, HISTORY)

    assert alert["level"] == "over"
    assert alert["remaining"] == -2470.0


def test_no_alert_for_project_without_budget():
    assert budget_alert(_spent(99999.0, project="مشروع جديد"), BUDGETS, HISTORY) is None


def test_load_budgets_reads_json_and_tolerates_missing_file(tmp_path):
    path = tmp_path / "budgets.json"
    path.write_text(json.dumps({"مستودع 6 أكتوبر": 50000}, ensure_ascii=False), encoding="utf-8")

    assert load_budgets(path) == {"مستودع 6 أكتوبر": 50000.0}
    assert load_budgets(tmp_path / "missing.json") == {}


def test_approval_request_warns_near_and_over_budget():
    expense = Expense(id=13, amount=18000.0, item="cement", project="فيلات التجمع الخامس")
    near = budget_alert(expense, BUDGETS, HISTORY)
    over = budget_alert(expense.model_copy(update={"amount": 30000.0}), BUDGETS, HISTORY)

    assert ("📊 لو وافقت، مشروع فيلات التجمع الخامس هيكون صرف 87% من ميزانيته "
            "(65,470 من 75,000 جنيه)، وفاضل 9,530 جنيه.") in owner_approval_request(expense, None, {}, budget=near)
    assert ("⚠️ لو وافقت، مشروع فيلات التجمع الخامس هيعدّي ميزانيته بـ 2,470 جنيه "
            "(هيوصل 77,470 من 75,000 جنيه).") in owner_approval_request(expense, None, {}, budget=over)


def test_budget_message_one_line_per_project():
    statuses = [budget_status("فيلات التجمع الخامس", 75000.0, 47470.0),
                budget_status("مستودع 6 أكتوبر", 50000.0, 52000.0)]

    assert budget_message(statuses) == (
        "📊 مشروع فيلات التجمع الخامس: صرفنا 47,470 من 75,000 جنيه (63%)، وفاضل 27,530 جنيه.\n"
        "📊 مشروع مستودع 6 أكتوبر: صرفنا 52,000 من 50,000 جنيه (104%)، وعدّينا الميزانية بـ 2,000 جنيه.")


def test_budget_message_for_unknown_project_and_no_budgets():
    assert budget_message([], unknown_project="مول") == "مفيش ميزانية متسجلة لمشروع مول."
    assert budget_message([]) == "مفيش ميزانيات مشاريع متسجلة."
