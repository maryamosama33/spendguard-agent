import sqlite3
from pathlib import Path

from spendguard.models import Expense

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "spendguard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    amount REAL,
    currency TEXT,
    supplier TEXT,
    project TEXT,
    cost_item TEXT,
    requester TEXT,
    invoice_number TEXT,
    confidence REAL,
    missing_fields TEXT,
    source_channel TEXT,
    sender TEXT,
    source_file TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def _row_to_expense(row: sqlite3.Row) -> Expense:
    data = dict(row)
    data["missing_fields"] = data["missing_fields"].split(",") if data["missing_fields"] else []
    return Expense(**data)


def insert_expense(conn: sqlite3.Connection, expense: Expense) -> int:
    values = expense.model_dump(exclude={"id", "missing_fields"})
    values["missing_fields"] = ",".join(expense.missing_fields)
    columns = ", ".join(values.keys())
    placeholders = ", ".join(f":{k}" for k in values)
    cursor = conn.execute(f"INSERT INTO expenses ({columns}) VALUES ({placeholders})", values)
    conn.commit()
    return cursor.lastrowid


def list_expenses(conn: sqlite3.Connection, statuses: list[str] | None = None) -> list[Expense]:
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        rows = conn.execute(
            f"SELECT * FROM expenses WHERE status IN ({placeholders})", statuses
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM expenses").fetchall()
    return [_row_to_expense(row) for row in rows]
