import hashlib
import os
from datetime import date
import shutil
import sqlite3
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from spendguard.models import Expense

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "spendguard.db"
DOCUMENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "documents"
SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"
DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
HERMES_MEDIA_CACHES = {("cache", "images"), ("cache", "documents")}

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
    quantity REAL,
    unit TEXT,
    requester TEXT,
    invoice_number TEXT,
    confidence REAL,
    missing_fields TEXT,
    source_channel TEXT,
    sender TEXT,
    source_file TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    rejection_reason TEXT,
    price_deviation_pct REAL,
    duplicate_of INTEGER,
    decided_at TEXT
);
"""

SHEET_COLUMNS = [
    "id", "date", "amount", "currency", "supplier", "project", "cost_item",
    "item", "requester", "invoice_number", "source_channel", "sender",
    "source_file", "status", "quantity", "unit",  # appended last: existing sheets keep their layout
]

# Columns added after the first release; init_db adds them to older databases.
ADDED_COLUMNS = {"quantity": "REAL", "unit": "TEXT", "price_deviation_pct": "REAL",
                 "duplicate_of": "INTEGER", "decided_at": "TEXT"}


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    _add_missing_columns(conn)
    conn.commit()


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(expenses)")}  # 1 = name
    for column, sql_type in ADDED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE expenses ADD COLUMN {column} {sql_type}")


def _row_to_expense(row: sqlite3.Row) -> Expense:
    data = dict(row)
    data["missing_fields"] = data["missing_fields"].split(",") if data["missing_fields"] else []
    return Expense(**data)


def insert_expense(conn: sqlite3.Connection, expense: Expense) -> int:
    values = expense.model_dump(exclude={"id", "missing_fields", "image_quality"})
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


KNOWN_VALUE_COLUMNS = {"item", "supplier", "project"}


def list_known_values(conn: sqlite3.Connection, column: str) -> list[str]:
    """Distinct values of item/supplier/project in approved history."""
    if column not in KNOWN_VALUE_COLUMNS:
        raise ValueError(f"Unsupported column: {column}")
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM expenses "
        f"WHERE status = 'approved' AND {column} IS NOT NULL ORDER BY {column}"
    ).fetchall()
    return [row[0] for row in rows]


def list_item_names(conn: sqlite3.Connection) -> list[str]:
    """Distinct item names in approved history, so extraction can reuse them."""
    return list_known_values(conn, "item")


def clear_expenses(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM expenses")
    conn.commit()


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
        "UPDATE expenses SET status = ?, rejection_reason = ?, decided_at = ? WHERE id = ?",
        (status, rejection_reason, date.today().isoformat(), expense_id),
    )
    conn.commit()


def _in_hermes_media_cache(path: Path) -> bool:
    return any((parent.parent.name, parent.name) in HERMES_MEDIA_CACHES for parent in path.parents)


def allowed_document(path: str | Path) -> bool:
    """An invoice photo/PDF uploaded through a chat (Hermes's media cache), a
    demo file in data/seed/, or an archived copy. Paths come from the chat
    model, which a sender can try to talk into reading or attaching any file
    on this machine (.env, credentials)."""
    resolved = Path(path).resolve()
    if resolved.suffix.lower() not in DOCUMENT_EXTENSIONS or not resolved.is_file():
        return False
    return (resolved.is_relative_to(SEED_DIR.resolve()) or resolved.is_relative_to(DOCUMENTS_DIR.resolve())
            or _in_hermes_media_cache(resolved))


def archive_document(source: str | None) -> str | None:
    """Copy the original document into data/documents/ (named by content hash,
    so re-saving the same file is a no-op) and return the copy's path. Chat
    gateways keep downloads in a cache that may be cleaned; the audit link
    must outlive it (F06). None if there is no allowed document at `source`."""
    if not source or not allowed_document(source):
        return None
    src = Path(source).resolve()
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    target = DOCUMENTS_DIR / f"{digest}{src.suffix.lower()}"
    if not target.exists():
        DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return str(target)


def _sheets_client() -> gspread.Client:
    creds_path = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def _worksheet() -> gspread.Worksheet:
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    return _sheets_client().open_by_key(sheet_id).sheet1


def sheets_configured() -> bool:
    return bool(os.environ.get("GOOGLE_SHEET_ID") and os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"))


def append_to_sheet(expense: Expense) -> bool:
    """Mirror an approved expense to Google Sheets. Returns False (and writes
    nothing) when Sheets isn't configured, so a demo without Google
    credentials still works; SQLite stays the source of truth."""
    if not sheets_configured():
        return False
    row = [str(getattr(expense, col) or "") for col in SHEET_COLUMNS]
    _worksheet().append_row(row)
    return True
