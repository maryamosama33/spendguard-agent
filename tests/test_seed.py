import sqlite3

import pytest

from spendguard.checks import check_price_anomaly
from spendguard.models import Expense
from spendguard.seed import _load_price_history, seed_price_history
from spendguard.storage import insert_expense, list_expenses


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def test_load_price_history_parses_as_approved_expenses():
    expenses = _load_price_history()

    assert len(expenses) > 0
    assert all(e.status == "approved" for e in expenses)
    assert all(e.amount is not None and e.supplier and e.item for e in expenses)


def test_seed_price_history_inserts_rows(conn):
    count = seed_price_history(conn)

    assert count == len(_load_price_history())
    assert len(list_expenses(conn)) == count


def test_seed_price_history_skips_if_data_exists(conn):
    seed_price_history(conn)
    before = len(list_expenses(conn))

    second_count = seed_price_history(conn)

    assert second_count == 0
    assert len(list_expenses(conn)) == before


def test_seed_price_history_force_reseeds(conn):
    seed_price_history(conn)
    before = len(list_expenses(conn))

    seed_price_history(conn, force=True)

    assert len(list_expenses(conn)) == before * 2


def test_seeded_steel_history_triggers_price_anomaly(conn):
    seed_price_history(conn)
    steel_history = [e for e in list_expenses(conn) if e.item == "steel rebar 12mm"]

    pricier_candidate = Expense(
        supplier="مصنع الحديد الوطني",
        item="steel rebar 12mm",
        amount=18000.0,
        date="2026-09-20",
    )

    result = check_price_anomaly(pricier_candidate, steel_history)

    assert result["is_anomaly"] is True
