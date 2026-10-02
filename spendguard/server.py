import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google.genai import errors as genai_errors
from mcp.server.mcpserver import MCPServer

from spendguard.checks import check_price_anomaly as _check_price_anomaly
from spendguard.checks import find_duplicate
from spendguard.decision import owner_decision
from spendguard.extraction import extract_expense as _extract_expense
from spendguard.extraction import extract_expense_from_text as _extract_expense_from_text
from spendguard.extraction import find_missing_fields
from spendguard.messages import (
    approved_message,
    missing_fields_question,
    owner_approval_request,
    rejected_message,
    unreadable_document_message,
)
from spendguard.models import Expense
from spendguard.normalize import canonical_name
from spendguard.query import filter_expenses, summarize
from spendguard.storage import (
    append_to_sheet,
    get_connection,
    get_expense,
    init_db,
    insert_expense,
    list_expenses,
    list_item_names,
    list_known_values,
    update_status,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_FILE = REPO_ROOT / "data" / "seed" / "projects.json"


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
        source_channel: "telegram", "whatsapp" or "email".
        sender: the user ID, phone number or email address the request came from.

    If fields are missing, the result includes "reply_to_sender": the
    Egyptian Arabic question to send as-is. If the document is unreadable
    (low confidence, or not even amount and supplier found), it is
    {"unreadable": True, "reply_to_sender": <ask for a clearer photo>}.

    On failure returns {"error": ..., "retryable": bool} instead of raising, so
    the agent can tell the sender what happened.
    """
    try:
        expense = _extract_expense(file_path, source_channel, sender, _known_items())
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}", "retryable": False}
    except genai_errors.APIError as e:
        return _extraction_service_error(e)
    if _is_unreadable(expense):
        return {"unreadable": True, "confidence": expense.confidence,
                "reply_to_sender": unreadable_document_message()}
    return _extraction_result(expense)


MIN_CONFIDENCE = 0.6


def _is_unreadable(expense: Expense) -> bool:
    """Too unsure to ask field-by-field questions: ask for a new photo instead (F08)."""
    # Gemini invents blurred digits (and reports high confidence), so any photo
    # it calls blurry is re-requested: a wrong amount is worse than asking twice.
    nothing_read = expense.amount is None and not expense.supplier
    return (expense.image_quality in ("blurry", "unreadable")
            or expense.confidence < MIN_CONFIDENCE
            or nothing_read)


@mcp.tool()
def extract_expense_from_text(text: str, source_channel: str, sender: str) -> dict:
    """Extract structured expense fields from a text request: a transcribed
    voice note or an email body (no attachment).

    Args:
        text: the request text, e.g. the voice-note transcript.
        source_channel: "telegram", "whatsapp" or "email".
        sender: the user ID, phone number or email address the request came from.

    Same result shape as extract_expense (including "reply_to_sender" when
    fields are missing, and {"error", "retryable"} on failure).
    """
    try:
        expense = _extract_expense_from_text(text, source_channel, sender, _known_items())
    except genai_errors.APIError as e:
        return _extraction_service_error(e)
    return _extraction_result(expense)


def _known_items() -> list[str]:
    """Item names from approved history, so extraction names the same item the same way
    and the price check can compare it (e.g. "نقل رمل" -> "sand transport")."""
    conn = get_connection()
    try:
        init_db(conn)
        return list_item_names(conn)
    finally:
        conn.close()


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
    duplicate = _find_duplicate_in_db(_normalized(expense))
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
    return _price_check_in_db(_normalized(expense))


def _known_projects(conn) -> list[str]:
    seeded = json.loads(PROJECTS_FILE.read_text(encoding="utf-8")) if PROJECTS_FILE.exists() else []
    return sorted(set(seeded) | set(list_known_values(conn, "project")))


def _normalized(expense: dict) -> Expense:
    """Expense with project/supplier snapped to known names, undoing the
    model's retyping (e.g. "فيلا التجمع الخامس" -> "فيلات التجمع الخامس")."""
    candidate = Expense(**expense)
    conn = get_connection()
    try:
        init_db(conn)
        candidate.project = canonical_name(candidate.project, _known_projects(conn))
        candidate.supplier = canonical_name(candidate.supplier, list_known_values(conn, "supplier"))
    finally:
        conn.close()
    return candidate


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
    candidate = _normalized(expense)
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


def _decision_refusal(expense: Expense | None, owner_message: str, wanted: str) -> dict | None:
    """Why this approve/reject must not happen, or None if it may."""
    if expense is None:
        return {"error": "No such expense id.", "decided": False}
    if expense.status != "pending":
        return {"error": f"Expense {expense.id} is already {expense.status}.", "decided": False}
    if owner_decision(owner_message) != wanted:
        return {
            "error": f"owner_message has no explicit owner {wanted} decision. Only the owner "
                     "decides: send the approval request and wait for their reply.",
            "decided": False,
        }
    return None


def _load_expense(expense_id: int) -> Expense | None:
    conn = get_connection()
    try:
        init_db(conn)
        return get_expense(conn, expense_id)
    finally:
        conn.close()


@mcp.tool()
def approve_expense(expense_id: int, owner_message: str) -> dict:
    """Approve a pending expense: marks it approved and writes the row to Google Sheets.

    Args:
        expense_id: the id returned by save_expense.
        owner_message: the owner's reply, word for word (e.g. "موافق"). Refused
            unless it explicitly approves.
    """
    expense = _load_expense(expense_id)
    if refusal := _decision_refusal(expense, owner_message, "approve"):
        return refusal
    conn = get_connection()
    try:
        update_status(conn, expense_id, "approved")
    finally:
        conn.close()

    expense.status = "approved"
    append_to_sheet(expense)
    return expense.model_dump() | {"reply": approved_message(expense)}


@mcp.tool()
def reject_expense(expense_id: int, reason: str, owner_message: str) -> dict:
    """Reject a pending expense and record the reason. Never writes to Sheets.

    Args:
        expense_id: the id returned by save_expense.
        reason: why the owner rejected it.
        owner_message: the owner's reply, word for word (e.g. "ارفضه، السعر
            عالي"). Refused unless it explicitly rejects.
    """
    expense = _load_expense(expense_id)
    if refusal := _decision_refusal(expense, owner_message, "reject"):
        return refusal
    conn = get_connection()
    try:
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
