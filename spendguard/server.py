import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google.genai import errors as genai_errors
from mcp.server.mcpserver import MCPServer

from spendguard.checks import check_price_anomaly as _check_price_anomaly
from spendguard.checks import find_duplicate, find_resubmission
from spendguard.decision import owner_decision
from spendguard.extraction import ExtractionFailed
from spendguard.extraction import extract_expense as _extract_expense
from spendguard.extraction import extract_expense_from_text as _extract_expense_from_text
from spendguard.extraction import find_missing_fields
from spendguard import notify
from spendguard.messages import (
    approved_message,
    forwarded_owner_request,
    missing_fields_question,
    owner_approval_request,
    rejected_message,
    requester_decision_message,
    sent_to_owner_message,
    unclear_decision_message,
    unreadable_document_message,
    which_expense_message,
)
from spendguard.models import Expense
from spendguard.normalize import canonical_name, resolve_name
from spendguard.query import filter_expenses, summarize
from spendguard.storage import (
    allowed_document,
    append_to_sheet,
    archive_document,
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
    if refusal := _document_refusal(file_path):
        return refusal
    try:
        expense = _extract_expense(file_path, source_channel, sender, _known_items())
    except genai_errors.APIError as e:
        return _extraction_service_error(e)
    except ExtractionFailed:
        return EXTRACTION_FAILED
    except ValueError as e:  # e.g. a file type Gemini can't be sent
        return {"error": f"Cannot read this document: {e}", "retryable": False}
    if _is_unreadable(expense):
        return {"unreadable": True, "confidence": expense.confidence,
                "reply_to_sender": unreadable_document_message()}
    return _extraction_result(expense)


def _document_refusal(file_path: str) -> dict | None:
    if not Path(file_path).is_file():
        return {"error": f"File not found: {file_path}", "retryable": False}
    if not allowed_document(file_path):
        return {"error": "Only invoice photos/PDFs (pdf, jpg, png, webp) sent in the chat or "
                         "placed in data/seed/invoices/ can be read.", "retryable": False}
    return None


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
    except ExtractionFailed:
        return EXTRACTION_FAILED
    return _extraction_result(expense)


EXTRACTION_FAILED = {"error": "The invoice-reading service gave an unusable answer. Try again.",
                     "retryable": True}


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
    """Check whether an expense looks like a repeat of an existing pending, approved or rejected one.

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
        # Rejected too: an invoice turned down once must not slip back in via someone else.
        existing = list_expenses(conn, statuses=["pending", "approved", "rejected"])
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
def save_expense(expense: dict, telegram_sender: str = "") -> dict:
    """Save an expense as pending, awaiting owner approval. Never writes to Sheets.

    The result includes the Egyptian Arabic text to send as-is: either
    "reply_to_owner" (the approval request, with duplicate/price warnings
    re-checked here) or, when a site engineer sent it on Telegram and it was
    forwarded to the owner's own chat, "reply_to_sender" (a confirmation).
    The source document is archived under data/documents/ and attached.
    Refuses to save if required fields are missing, returning
    {"saved": False, "missing_fields", "reply_to_sender"} instead.

    Args:
        expense: an expense dict, e.g. the output of extract_expense.
        telegram_sender: set by the system; leave it empty.
    """
    candidate = _normalized(expense)
    candidate.missing_fields = find_missing_fields(candidate)
    if candidate.missing_fields:
        return _not_saved_missing_fields(candidate)
    candidate.status = "pending"
    candidate.source_file = archive_document(candidate.source_file)
    if telegram_sender:  # the verified ID, so the decision can be sent back to them
        candidate.source_channel, candidate.sender = "telegram", telegram_sender
    if earlier := _pending_resubmission(candidate):
        candidate.id = earlier.id
        return _saved_result(candidate, telegram_sender, forward=False) | {"already_saved": True}
    conn = get_connection()
    try:
        init_db(conn)
        candidate.id = insert_expense(conn, candidate)
    finally:
        conn.close()
    return _saved_result(candidate, telegram_sender, forward=True)


def _pending_resubmission(candidate: Expense) -> Expense | None:
    conn = get_connection()
    try:
        init_db(conn)
        pending = list_expenses(conn, statuses=["pending"])
    finally:
        conn.close()
    return find_resubmission(candidate, pending)


def _saved_result(candidate: Expense, telegram_sender: str, forward: bool) -> dict:
    """The saved expense plus the reply. A retried save (forward=False) doesn't
    message the owner a second time."""
    duplicate = _find_duplicate_in_db(candidate)
    anomaly = _price_check_in_db(candidate)
    if forward:
        with_owner = _forwarded_to_owner(candidate, duplicate, anomaly, telegram_sender)
    else:
        with_owner = _is_engineer_on_telegram(telegram_sender)  # the first save sent it
    result = candidate.model_dump()
    if with_owner:
        result["reply_to_sender"] = sent_to_owner_message(candidate)
    else:
        # MEDIA: tags only mean something to the Telegram gateway; the terminal would print them.
        source = "attach" if telegram_sender else "note"
        result["reply_to_owner"] = owner_approval_request(candidate, duplicate, anomaly, source)
    return result


def _is_engineer_on_telegram(telegram_sender: str) -> bool:
    return bool(telegram_sender) and not notify.is_owner(telegram_sender) and notify.telegram_configured()


def _forwarded_to_owner(candidate: Expense, duplicate: Expense | None, anomaly: dict,
                        telegram_sender: str) -> bool:
    """Send an engineer's request to the owner's own Telegram chat (F14). The
    owner's own requests, and terminal/email ones, are answered in place."""
    if not _is_engineer_on_telegram(telegram_sender):
        return False
    text = forwarded_owner_request(candidate, duplicate, anomaly)
    return notify.send_to_owners(text, candidate.source_file)


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
    if refusal := _wrong_expense_refusal(expense.id, owner_message):
        return refusal
    if owner_decision(owner_message) != wanted:
        return {
            "error": f"owner_message has no explicit owner {wanted} decision. Only the owner "
                     "decides: send the approval request and wait for their reply.",
            "decided": False,
            "reply": unclear_decision_message(expense.id),
        }
    return None


def _pending_ids() -> list[int]:
    conn = get_connection()
    try:
        init_db(conn)
        return sorted(e.id for e in list_expenses(conn, statuses=["pending"]))
    finally:
        conn.close()


def _wrong_expense_refusal(expense_id: int, owner_message: str) -> dict | None:
    """The owner names another pending request, or names none while several
    wait: never let the model pick which expense the owner meant."""
    pending = _pending_ids()
    named = {int(n) for n in re.findall(r"\d+", owner_message or "")} & set(pending)
    if expense_id in named or (not named and pending == [expense_id]):
        return None
    if named:
        return {"error": f"The owner named request {sorted(named)}, not {expense_id}.",
                "decided": False}
    return {"error": "Several expenses are pending and the owner named none.",
            "decided": False, "reply": which_expense_message(pending)}


def _notify_requester(expense: Expense, telegram_sender: str) -> bool:
    """Tell the engineer who sent it (on Telegram) what the owner decided."""
    if (expense.source_channel != "telegram" or not (expense.sender or "").isdigit()
            or expense.sender == telegram_sender or not notify.telegram_configured()):
        return False
    return notify.send_message(expense.sender, requester_decision_message(expense))


def _load_expense(expense_id: int) -> Expense | None:
    conn = get_connection()
    try:
        init_db(conn)
        return get_expense(conn, expense_id)
    finally:
        conn.close()


@mcp.tool()
def approve_expense(expense_id: int, owner_message: str, telegram_sender: str = "") -> dict:
    """Approve a pending expense: marks it approved and writes the row to Google Sheets.

    Args:
        expense_id: the request number the owner gave (e.g. 12 in "موافق 12"),
            or the id returned by save_expense.
        owner_message: the owner's reply, word for word (e.g. "موافق 12").
            Refused unless it explicitly approves this request.
        telegram_sender: set by the system; leave it empty.
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
    synced = append_to_sheet(expense)
    return expense.model_dump() | {"sheet_synced": synced, "reply": approved_message(expense, synced),
                                   "requester_notified": _notify_requester(expense, telegram_sender)}


@mcp.tool()
def reject_expense(expense_id: int, reason: str, owner_message: str, telegram_sender: str = "") -> dict:
    """Reject a pending expense and record the reason. Never writes to Sheets.

    Args:
        expense_id: the request number the owner gave (e.g. 12 in "ارفض 12"),
            or the id returned by save_expense.
        reason: why the owner rejected it.
        owner_message: the owner's reply, word for word (e.g. "ارفض 12، السعر
            عالي"). Refused unless it explicitly rejects this request.
        telegram_sender: set by the system; leave it empty.
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
    return expense.model_dump() | {"reply": rejected_message(expense),
                                   "requester_notified": _notify_requester(expense, telegram_sender)}


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
        project, supplier, item = _canonical_filters(conn, project, supplier, item)
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
    summary["filters"] = {"project": project, "supplier": supplier, "item": item}
    summary["expenses"] = [e.model_dump() for e in matches]
    return summary


def _canonical_filters(conn, project: str | None, supplier: str | None,
                       item: str | None) -> tuple[str | None, str | None, str | None]:
    """The question's names snapped to known ones ("فيلا التجمع" -> "فيلات التجمع
    الخامس"), since the filters match exactly."""
    return (resolve_name(project, _known_projects(conn)),
            resolve_name(supplier, list_known_values(conn, "supplier")),
            resolve_name(item, list_item_names(conn)))


if __name__ == "__main__":
    mcp.run()
