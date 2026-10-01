import sqlite3

import pytest

from spendguard.models import Expense
from spendguard.storage import init_db, insert_expense, list_expenses


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
