from mcp.server.mcpserver import MCPServer

from spendguard.checks import find_duplicate
from spendguard.extraction import extract_expense as _extract_expense
from spendguard.models import Expense
from spendguard.storage import get_connection, init_db, list_expenses

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


if __name__ == "__main__":
    mcp.run()
