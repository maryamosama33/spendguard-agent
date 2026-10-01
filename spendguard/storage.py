import os
import sqlite3
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

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
    item TEXT,
    requester TEXT,
    invoice_number TEXT,
    confidence REAL,
    missing_fields TEXT,
    source_channel TEXT,
    sender TEXT,
    source_file TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    rejection_reason TEXT
);
"""

SHEET_COLUMNS = [
    "id", "date", "amount", "currency", "supplier", "project", "cost_item",
    "item", "requester", "invoice_number", "source_channel", "sender",
    "source_file", "status",
]


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


def _expenses_query(statuses: list[str] | None, supplier: str | None) -> tuple[str, list]:
    clauses, params = [], []
    if statuses:
        clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if supplier:
        clauses.append("supplier = ?")
        params.append(supplier)
    query = "SELECT * FROM expenses"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    return query, params


def list_expenses(
    conn: sqlite3.Connection,
    statuses: list[str] | None = None,
    supplier: str | None = None,
) -> list[Expense]:
    query, params = _expenses_query(statuses, supplier)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_expense(row) for row in rows]


def get_expense(conn: sqlite3.Connection, expense_id: int) -> Expense | None:
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    return _row_to_expense(row) if row else None


def update_status(
    conn: sqlite3.Connection,
    expense_id: int,
    status: str,
    rejection_reason: str | None = None,
) -> None:
    conn.execute(
        "UPDATE expenses SET status = ?, rejection_reason = ? WHERE id = ?",
        (status, rejection_reason, expense_id),
    )
    conn.commit()


def _sheets_client() -> gspread.Client:
    creds_path = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def _worksheet() -> gspread.Worksheet:
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    return _sheets_client().open_by_key(sheet_id).sheet1


def append_to_sheet(expense: Expense) -> None:
    row = [str(getattr(expense, col) or "") for col in SHEET_COLUMNS]
    _worksheet().append_row(row)
