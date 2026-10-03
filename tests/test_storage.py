import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from spendguard.models import Expense
from spendguard.storage import (
    append_to_sheet,
    get_expense,
    init_db,
    insert_expense,
    list_expenses,
    list_item_names,
    update_status,
)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    yield connection
    connection.close()


def test_insert_and_list_round_trip(conn):
    expense = Expense(
        date="2026-09-30",
        amount=3000.0,
        supplier="Al-Nasr Sand Co",
        missing_fields=["project"],
    )

    expense_id = insert_expense(conn, expense)
    [stored] = list_expenses(conn)

    assert expense_id == stored.id
    assert stored.amount == 3000.0
    assert stored.missing_fields == ["project"]
    assert stored.status == "pending"


def test_list_expenses_filters_by_status(conn):
    insert_expense(conn, Expense(supplier="A", status="pending"))
    insert_expense(conn, Expense(supplier="B", status="rejected"))

    pending_only = list_expenses(conn, statuses=["pending"])

    assert [e.supplier for e in pending_only] == ["A"]


def test_list_expenses_empty_db_returns_empty_list(conn):
    assert list_expenses(conn) == []


def test_list_expenses_filters_by_supplier(conn):
    insert_expense(conn, Expense(supplier="Al-Nasr Sand Co"))
    insert_expense(conn, Expense(supplier="Other Co"))

    matches = list_expenses(conn, supplier="Al-Nasr Sand Co")

    assert [e.supplier for e in matches] == ["Al-Nasr Sand Co"]


def test_list_item_names_distinct_from_approved_only(conn):
    for item, status in [("cement", "approved"), ("cement", "approved"),
                         ("sand transport", "approved"), ("بلاط", "pending"), (None, "approved")]:
        insert_expense(conn, Expense(item=item, status=status))

    assert list_item_names(conn) == ["cement", "sand transport"]


def test_get_expense_found(conn):
    expense_id = insert_expense(conn, Expense(supplier="A"))

    found = get_expense(conn, expense_id)

    assert found is not None
    assert found.id == expense_id


def test_get_expense_not_found(conn):
    assert get_expense(conn, 999) is None


def test_update_status_to_approved(conn):
    expense_id = insert_expense(conn, Expense(supplier="A"))

    update_status(conn, expense_id, "approved")

    assert get_expense(conn, expense_id).status == "approved"


def test_update_status_to_rejected_records_reason(conn):
    expense_id = insert_expense(conn, Expense(supplier="A"))

    update_status(conn, expense_id, "rejected", rejection_reason="duplicate")
    expense = get_expense(conn, expense_id)

    assert expense.status == "rejected"
    assert expense.rejection_reason == "duplicate"


def test_init_db_adds_quantity_and_unit_to_an_older_database():
    old = sqlite3.connect(":memory:")
    old.row_factory = sqlite3.Row
    old.execute("CREATE TABLE expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, amount REAL, "
                "currency TEXT, supplier TEXT, project TEXT, cost_item TEXT, item TEXT, requester TEXT, "
                "invoice_number TEXT, confidence REAL, missing_fields TEXT, source_channel TEXT, "
                "sender TEXT, source_file TEXT, status TEXT NOT NULL DEFAULT 'pending', rejection_reason TEXT)")

    init_db(old)
    init_db(old)  # idempotent
    expense_id = insert_expense(old, Expense(amount=30000.0, quantity=2, unit="ton"))

    saved = get_expense(old, expense_id)
    assert (saved.quantity, saved.unit) == (2.0, "ton")
    old.close()


def test_append_to_sheet_skipped_without_sheets_config(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)

    with patch("spendguard.storage._worksheet") as worksheet:
        assert append_to_sheet(Expense(id=1, status="approved")) is False

    worksheet.assert_not_called()


def test_append_to_sheet_writes_expected_row(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "creds.json")
    expense = Expense(id=1, date="2026-09-30", amount=1200.0, supplier="Al-Nasr Sand Co", status="approved")
    mock_worksheet = MagicMock()

    with patch("spendguard.storage._worksheet", return_value=mock_worksheet):
        assert append_to_sheet(expense) is True

    mock_worksheet.append_row.assert_called_once()
    row = mock_worksheet.append_row.call_args[0][0]
    assert row[0] == "1"
    assert "Al-Nasr Sand Co" in row
