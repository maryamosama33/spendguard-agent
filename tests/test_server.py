from unittest.mock import patch

import pytest
from google.genai import errors as genai_errors

from spendguard import server, storage
from spendguard.models import Expense
from spendguard.seed import seed_price_history


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    conn = storage.get_connection()
    seed_price_history(conn)
    conn.close()


STEEL = dict(
    date="2026-10-01", amount=18000.0, supplier="مصنع الحديد الوطني",
    project="فيلات التجمع الخامس", cost_item="materials", item="steel rebar 12mm",
    requester="محمد حسن", invoice_number="NSF-2241", reply_to_sender="ignored extra key",
)


def test_save_expense_reply_includes_price_warning(seeded_db):
    result = server.save_expense(STEEL)

    assert result["status"] == "pending"
    assert result["reply_to_owner"].startswith(f"طلب صرف جديد رقم {result['id']} من محمد حسن")
    assert "⚠️ السعر أعلى بـ 20%" in result["reply_to_owner"]
    assert "اتقدمت قبل كده" not in result["reply_to_owner"]  # must not match its own row


def test_save_expense_refuses_incomplete_expense(seeded_db):
    retyped = {k: v for k, v in STEEL.items() if k != "requester"} | {"missing_fields": []}

    result = server.save_expense(retyped)

    assert result["saved"] is False
    assert result["missing_fields"] == ["requester"]
    assert "مين طالب الصرف" in result["reply_to_sender"]
    conn = storage.get_connection()
    assert len(storage.list_expenses(conn, statuses=["pending"])) == 0
    conn.close()


def test_save_expense_snaps_retyped_project_to_known_name(seeded_db):
    result = server.save_expense(STEEL | {"project": "فيلا التجمع الخامس"})

    assert result["project"] == "فيلات التجمع الخامس"


def test_reject_refused_without_owner_decision(seeded_db):
    saved = server.save_expense(STEEL)

    result = server.reject_expense(saved["id"], "duplicate", owner_message="رسالة جديدة ومعاها فاتورة")

    assert result["decided"] is False
    assert storage.get_expense(storage.get_connection(), saved["id"]).status == "pending"


def test_owner_rejection_recorded(seeded_db):
    saved = server.save_expense(STEEL)

    result = server.reject_expense(saved["id"], "السعر عالي", owner_message="ارفضه، السعر عالي")

    assert result["status"] == "rejected"
    assert result["reply"] == f"تمام، اترفض طلب رقم {saved['id']}. السبب: السعر عالي"


def test_owner_approval_writes_sheet_once(seeded_db):
    saved = server.save_expense(STEEL)

    with patch("spendguard.server.append_to_sheet") as sheet:
        first = server.approve_expense(saved["id"], owner_message="موافق")
        second = server.approve_expense(saved["id"], owner_message="موافق")

    assert first["status"] == "approved"
    assert second["decided"] is False and "already approved" in second["error"]
    sheet.assert_called_once()


def test_approve_refused_when_owner_said_no(seeded_db):
    saved = server.save_expense(STEEL)

    with patch("spendguard.server.append_to_sheet") as sheet:
        result = server.approve_expense(saved["id"], owner_message="مش موافق")

    assert result["decided"] is False
    sheet.assert_not_called()


def test_save_expense_reply_warns_on_resubmitted_invoice(seeded_db):
    server.save_expense(STEEL)

    result = server.save_expense(STEEL)

    assert "⚠️ الفاتورة دي اتقدمت قبل كده: NSF-2241" in result["reply_to_owner"]


def _api_error(code: int) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"code": code, "message": "x"}})


def test_extract_expense_quota_error_is_retryable_dict():
    with patch("spendguard.server._extract_expense", side_effect=_api_error(429)):
        result = server.extract_expense("invoice.pdf", "email", "a@b.example")

    assert result["retryable"] is True
    assert "429" in result["error"]


def test_extract_expense_bad_request_is_not_retryable():
    with patch("spendguard.server._extract_expense", side_effect=_api_error(400)):
        result = server.extract_expense("invoice.pdf", "email", "a@b.example")

    assert result["retryable"] is False


def test_extract_expense_from_text_quota_error_is_retryable_dict():
    with patch("spendguard.server._extract_expense_from_text", side_effect=_api_error(503)):
        result = server.extract_expense_from_text("دفعت 3000 جنيه", "whatsapp", "201")

    assert result["retryable"] is True


def _extract_with(**fields):
    expense = Expense(**fields)
    with patch("spendguard.server._extract_expense", return_value=expense), \
         patch("spendguard.server._known_items", return_value=[]):
        return server.extract_expense("photo.jpg", "telegram", "1386120774")


def test_low_confidence_photo_asks_for_new_photo():
    result = _extract_with(amount=860.0, supplier="النصر", confidence=0.3)

    assert result["unreadable"] is True
    assert "تصورها تاني" in result["reply_to_sender"]


def test_photo_marked_unreadable_asks_for_new_photo_even_if_confident():
    # Gemini can invent blurry digits with high self-reported confidence.
    result = _extract_with(amount=1100.0, supplier="شركة أسمنت السويس", confidence=0.85,
                           image_quality="unreadable")

    assert result["unreadable"] is True


def test_blurry_photo_asks_for_new_photo():
    result = _extract_with(amount=105.0, supplier="شركة أسمنت السويس", confidence=0.85,
                           image_quality="blurry")

    assert result["unreadable"] is True


def test_clear_photo_is_read():
    result = _extract_with(amount=860.0, supplier="النصر", confidence=0.95, image_quality="clear")

    assert "unreadable" not in result


def test_photo_with_nothing_read_asks_for_new_photo():
    result = _extract_with(confidence=0.9)

    assert result["unreadable"] is True


def test_clear_photo_with_some_missing_fields_asks_for_fields():
    result = _extract_with(amount=860.0, supplier="النصر", confidence=0.9, missing_fields=["project"])

    assert "unreadable" not in result
    assert "المشروع" in result["reply_to_sender"]


def test_extract_expense_missing_file_returns_error():
    result = server.extract_expense("does/not/exist.pdf", "whatsapp", "201")

    assert result == {"error": "File not found: does/not/exist.pdf", "retryable": False}
