from mcp.server.mcpserver import MCPServer

from spendguard.checks import check_price_anomaly as _check_price_anomaly
from spendguard.checks import find_duplicate
from spendguard.extraction import extract_expense as _extract_expense
from spendguard.models import Expense
from spendguard.query import filter_expenses, summarize
from spendguard.storage import (
    append_to_sheet,
    get_connection,
    get_expense,
    init_db,
    insert_expense,
    list_expenses,
    update_status,
)

mcp = MCPServer("spendguard")


@mcp.tool()
def extract_expense(file_path: str, source_channel: str, sender: str) -> dict:
    """Extract structured expense fields from a photo, PDF, or scanned form.

    Args:
        file_path: path to the image/PDF to read.
        source_channel: "whatsapp" or "email".
        sender: the WhatsApp number or email address the request came from.
    """
    return _extract_expense(file_path, source_channel, sender).model_dump()


@mcp.tool()
def check_duplicate(expense: dict) -> dict:
    """Check whether an expense looks like a repeat of an existing pending/approved one.

    Args:
        expense: an expense dict, e.g. the output of extract_expense.
    """
    candidate = Expense(**expense)
    conn = get_connection()
    try:
        init_db(conn)
        existing = list_expenses(conn, statuses=["pending", "approved"])
    finally:
        conn.close()

    duplicate = find_duplicate(candidate, existing)
    return {
        "is_duplicate": duplicate is not None,
        "matched_expense": duplicate.model_dump() if duplicate else None,
    }


@mcp.tool()
def check_price_anomaly(expense: dict) -> dict:
    """Flag if the price is abnormally high vs. recent purchases of the same item/supplier.

    Args:
        expense: an expense dict, e.g. the output of extract_expense.
    """
    candidate = Expense(**expense)
    conn = get_connection()
    try:
        init_db(conn)
        history = list_expenses(conn, statuses=["approved"], supplier=candidate.supplier)
    finally:
        conn.close()

    return _check_price_anomaly(candidate, history)


@mcp.tool()
def save_expense(expense: dict) -> dict:
    """Save an expense as pending, awaiting owner approval. Never writes to Sheets.

    Args:
        expense: an expense dict, e.g. the output of extract_expense.
    """
    candidate = Expense(**expense)
    candidate.status = "pending"
    conn = get_connection()
    try:
        init_db(conn)
        candidate.id = insert_expense(conn, candidate)
    finally:
        conn.close()

    return candidate.model_dump()


def _require_expense(conn, expense_id: int) -> Expense:
    expense = get_expense(conn, expense_id)
    if expense is None:
        raise ValueError(f"No expense with id {expense_id}")
    return expense


@mcp.tool()
def approve_expense(expense_id: int) -> dict:
    """Approve a pending expense: marks it approved and writes the row to Google Sheets.

    Args:
        expense_id: the id returned by save_expense.
    """
    conn = get_connection()
    try:
        init_db(conn)
        expense = _require_expense(conn, expense_id)
        update_status(conn, expense_id, "approved")
    finally:
        conn.close()

    expense.status = "approved"
    append_to_sheet(expense)
    return expense.model_dump()


@mcp.tool()
def reject_expense(expense_id: int, reason: str) -> dict:
    """Reject a pending expense and record the reason. Never writes to Sheets.

    Args:
        expense_id: the id returned by save_expense.
        reason: why it was rejected.
    """
    conn = get_connection()
    try:
        init_db(conn)
        expense = _require_expense(conn, expense_id)
        update_status(conn, expense_id, "rejected", rejection_reason=reason)
    finally:
        conn.close()

    expense.status = "rejected"
    expense.rejection_reason = reason
    return expense.model_dump()


@mcp.tool()
def query_expenses(
    project: str | None = None,
    supplier: str | None = None,
    cost_item: str | None = None,
    item: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str = "approved",
) -> dict:
    """Answer spending questions: total, count, and per-cost-item breakdown.

    Args:
        project, supplier, cost_item, item: optional exact-match filters
            (case-insensitive).
        date_from, date_to: optional ISO (YYYY-MM-DD) date bounds, inclusive.
        status: which expenses to include, default "approved" (actual spend).
    """
    conn = get_connection()
    try:
        init_db(conn)
        candidates = list_expenses(conn, statuses=[status])
    finally:
        conn.close()

    matches = filter_expenses(
        candidates,
        project=project,
        supplier=supplier,
        cost_item=cost_item,
        item=item,
        date_from=date_from,
        date_to=date_to,
    )
    summary = summarize(matches)
    summary["expenses"] = [e.model_dump() for e in matches]
    return summary


if __name__ == "__main__":
    mcp.run()
