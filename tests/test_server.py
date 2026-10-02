from unittest.mock import patch

import pytest
from google.genai import errors as genai_errors

from spendguard import server, storage
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


def test_extract_expense_missing_file_returns_error():
    result = server.extract_expense("does/not/exist.pdf", "whatsapp", "201")

    assert result == {"error": "File not found: does/not/exist.pdf", "retryable": False}
