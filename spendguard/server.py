import os
from pathlib import Path

from dotenv import load_dotenv
from google.genai import errors as genai_errors
from mcp.server.mcpserver import MCPServer

from spendguard.checks import check_price_anomaly as _check_price_anomaly
from spendguard.checks import find_duplicate
from spendguard.extraction import extract_expense as _extract_expense
from spendguard.extraction import extract_expense_from_text as _extract_expense_from_text
from spendguard.extraction import find_missing_fields
from spendguard.messages import (
    approved_message,
    missing_fields_question,
    owner_approval_request,
    rejected_message,
)
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

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Load .env by absolute path and resolve a relative service-account path,
    since this process is spawned by Hermes with an unknown working directory."""
    load_dotenv(REPO_ROOT / ".env")
    creds_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if creds_path and not Path(creds_path).is_absolute():
        os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"] = str(REPO_ROOT / creds_path)


_load_env()

mcp = MCPServer("spendguard")


@mcp.tool()
def extract_expense(file_path: str, source_channel: str, sender: str) -> dict:
    """Extract structured expense fields from a photo, PDF, or scanned form.

    Args:
        file_path: path to the image/PDF to read.
        source_channel: "whatsapp" or "email".
        sender: the WhatsApp number or email address the request came from.

    If fields are missing, the result includes "reply_to_sender": the
    Egyptian Arabic question to send as-is.

    On failure returns {"error": ..., "retryable": bool} instead of raising, so
    the agent can tell the sender what happened.
    """
    try:
        expense = _extract_expense(file_path, source_channel, sender)
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}", "retryable": False}
    except genai_errors.APIError as e:
        return _extraction_service_error(e)
    return _extraction_result(expense)


@mcp.tool()
def extract_expense_from_text(text: str, source_channel: str, sender: str) -> dict:
    """Extract structured expense fields from a text request: a transcribed
    voice note or an email body (no attachment).

    Args:
        text: the request text, e.g. the voice-note transcript.
        source_channel: "whatsapp" or "email".
        sender: the WhatsApp number or email address the request came from.

    Same result shape as extract_expense (including "reply_to_sender" when
    fields are missing, and {"error", "retryable"} on failure).
    """
    try:
        expense = _extract_expense_from_text(text, source_channel, sender)
    except genai_errors.APIError as e:
        return _extraction_service_error(e)
    return _extraction_result(expense)


def _extraction_result(expense: Expense) -> dict:
    result = expense.model_dump()
    if expense.missing_fields:
        result["reply_to_sender"] = missing_fields_question(expense)
    return result


def _extraction_service_error(e: genai_errors.APIError) -> dict:
    retryable = e.code in (429, 500, 502, 503, 504)
    return {
        "error": f"Invoice-reading service unavailable (HTTP {e.code}). "
        + ("Try again in a few minutes." if retryable else "Cannot read this document."),
        "retryable": retryable,
    }


@mcp.tool()
def check_duplicate(expense: dict) -> dict:
    """Check whether an expense looks like a repeat of an existing pending/approved one.

    Args:
        expense: an expense dict, e.g. the output of extract_expense.
    """
    duplicate = _find_duplicate_in_db(Expense(**expense))
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
    return _price_check_in_db(Expense(**expense))


def _find_duplicate_in_db(candidate: Expense) -> Expense | None:
    conn = get_connection()
    try:
        init_db(conn)
        existing = list_expenses(conn, statuses=["pending", "approved"])
    finally:
        conn.close()
    return find_duplicate(candidate, existing)


def _price_check_in_db(candidate: Expense) -> dict:
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

    The result includes "reply_to_owner": the Egyptian Arabic approval request
    (with any duplicate/price warnings, re-checked here) to send as-is.
    Refuses to save if required fields are missing, returning
    {"saved": False, "missing_fields", "reply_to_sender"} instead.

    Args:
        expense: an expense dict, e.g. the output of extract_expense.
    """
    candidate = Expense(**expense)
    candidate.missing_fields = find_missing_fields(candidate)
    if candidate.missing_fields:
        return _not_saved_missing_fields(candidate)
    candidate.status = "pending"
    conn = get_connection()
    try:
        init_db(conn)
        candidate.id = insert_expense(conn, candidate)
    finally:
        conn.close()

    duplicate = _find_duplicate_in_db(candidate)
    anomaly = _price_check_in_db(candidate)
    result = candidate.model_dump()
    result["reply_to_owner"] = owner_approval_request(candidate, duplicate, anomaly)
    return result


def _not_saved_missing_fields(candidate: Expense) -> dict:
    return {
        "saved": False,
        "missing_fields": candidate.missing_fields,
        "reply_to_sender": missing_fields_question(candidate),
    }


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
    return expense.model_dump() | {"reply": approved_message(expense)}


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
    return expense.model_dump() | {"reply": rejected_message(expense)}


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
