import json
import sqlite3
import sys
from pathlib import Path

from spendguard.models import Expense
from spendguard.storage import get_connection, init_db, insert_expense, list_expenses

PRICE_HISTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "seed" / "price_history.json"


def _load_price_history(path: Path = PRICE_HISTORY_FILE) -> list[Expense]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Expense(**row, status="approved") for row in rows]


def seed_price_history(conn: sqlite3.Connection, force: bool = False) -> int:
    """Load seed/price_history.json as approved expenses. Skips if the DB
    already has data, unless force=True."""
    init_db(conn)
    if list_expenses(conn) and not force:
        return 0

    expenses = _load_price_history()
    for expense in expenses:
        insert_expense(conn, expense)
    return len(expenses)


if __name__ == "__main__":
    conn = get_connection()
    try:
        count = seed_price_history(conn, force="--force" in sys.argv)
    finally:
        conn.close()

    if count:
        print(f"Seeded {count} approved historical expenses.")
    else:
        print("Database already has expenses; skipping (use --force to reseed).")
