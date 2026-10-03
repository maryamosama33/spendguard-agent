from pathlib import Path
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


def test_save_expense_archives_source_document_and_attaches_it(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DOCUMENTS_DIR", tmp_path / "documents")
    original = tmp_path / "cache" / "invoice.PDF"
    original.parent.mkdir()
    original.write_bytes(b"%PDF-1.4 steel")

    result = server.save_expense(STEEL | {"source_file": str(original)})

    archived = Path(result["source_file"])
    assert archived.parent == storage.DOCUMENTS_DIR
    assert archived.read_bytes() == b"%PDF-1.4 steel"
    assert archived.suffix == ".pdf"
    assert f"MEDIA:{result['source_file']}" in result["reply_to_owner"]
    original.unlink()  # the gateway cache is cleaned; the archived copy remains
    assert archived.exists()


def test_archive_document_is_idempotent_and_skips_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DOCUMENTS_DIR", tmp_path / "documents")
    original = tmp_path / "photo.jpg"
    original.write_bytes(b"jpeg")

    first = storage.archive_document(str(original))

    assert storage.archive_document(first) == first
    assert len(list(storage.DOCUMENTS_DIR.iterdir())) == 1
    assert storage.archive_document(str(tmp_path / "gone.jpg")) == str(tmp_path / "gone.jpg")
    assert storage.archive_document(None) is None


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


def test_seeded_steel_price_compared_per_ton(seeded_db):
    result = server.save_expense(STEEL | {"quantity": 1, "unit": "ton"})

    assert "⚠️ سعر الطن أعلى بـ 20%" in result["reply_to_owner"]


def test_two_tons_at_the_usual_price_raise_no_warning(seeded_db):
    result = server.save_expense(STEEL | {"quantity": 2, "unit": "ton", "amount": 30000.0})

    assert "⚠️" not in result["reply_to_owner"]


OWNER, ENGINEER = "1386120774", "555"


@pytest.fixture
def telegram(monkeypatch):
    """Telegram configured, with the Bot API calls recorded instead of sent."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("SPENDGUARD_OWNER_IDS", OWNER)
    sent = {"owners": [], "chats": []}
    monkeypatch.setattr(server.notify, "send_to_owners",
                        lambda text, document=None: sent["owners"].append((text, document)) or True)
    monkeypatch.setattr(server.notify, "send_message",
                        lambda chat, text: sent["chats"].append((chat, text)) or True)
    return sent


def test_engineer_request_is_forwarded_to_owner_chat(seeded_db, telegram):
    result = server.save_expense(STEEL | {"sender": "made up by the model"}, telegram_sender=ENGINEER)

    [(text, _)] = telegram["owners"]
    assert f"رد بـ «موافق {result['id']}»" in text and "⚠️ السعر أعلى" in text
    assert "MEDIA:" not in text
    assert "reply_to_owner" not in result
    assert "اتبعت لصاحب الشركة" in result["reply_to_sender"]
    assert (result["sender"], result["source_channel"]) == (ENGINEER, "telegram")


def test_owner_own_request_is_answered_in_place(seeded_db, telegram):
    result = server.save_expense(STEEL, telegram_sender=OWNER)

    assert telegram["owners"] == []
    assert result["reply_to_owner"].endswith("موافق ولا مرفوض؟")


def test_failed_forward_falls_back_to_reply_in_place(seeded_db, telegram, monkeypatch):
    monkeypatch.setattr(server.notify, "send_to_owners", lambda text, document=None: False)

    result = server.save_expense(STEEL, telegram_sender=ENGINEER)

    assert "reply_to_owner" in result and "reply_to_sender" not in result


def test_owner_decision_is_sent_back_to_engineer(seeded_db, telegram):
    saved = server.save_expense(STEEL, telegram_sender=ENGINEER)

    with patch("spendguard.server.append_to_sheet", return_value=False):
        result = server.approve_expense(saved["id"], f"موافق {saved['id']}", telegram_sender=OWNER)

    assert result["requester_notified"] is True
    assert telegram["chats"] == [(ENGINEER, f"صاحب الشركة وافق على طلبك رقم {saved['id']} (18,000 جنيه).")]


def test_decision_without_number_refused_when_several_pending(seeded_db):
    first = server.save_expense(STEEL)
    second = server.save_expense(STEEL | {"invoice_number": "NSF-9999", "date": "2026-10-02"})

    result = server.approve_expense(second["id"], "موافق")

    assert result["decided"] is False
    assert f"({first['id']}، {second['id']})" in result["reply"]


def test_decision_refused_when_owner_named_another_request(seeded_db):
    first = server.save_expense(STEEL)
    second = server.save_expense(STEEL | {"invoice_number": "NSF-9999", "date": "2026-10-02"})

    result = server.reject_expense(second["id"], "عالي", f"ارفض {first['id']}")

    assert result["decided"] is False
    assert storage.get_expense(storage.get_connection(), second["id"]).status == "pending"


def test_rejected_invoice_resubmitted_by_someone_else_is_flagged(seeded_db):
    first = server.save_expense(STEEL)
    server.reject_expense(first["id"], "السعر عالي", f"ارفض {first['id']}")

    again = server.save_expense(STEEL | {"requester": "أحمد علي", "invoice_number": "nsf 2241"})

    assert "⚠️ الفاتورة دي اتقدمت قبل كده: NSF-2241 بتاريخ 2026-10-01 (اترفضت: السعر عالي)." in again["reply_to_owner"]


def test_unclear_decision_asks_owner_again(seeded_db):
    saved = server.save_expense(STEEL)

    result = server.reject_expense(saved["id"], "x", "ماشي بس لا تكررها")

    assert result["decided"] is False
    assert f"«ارفض {saved['id']}»" in result["reply"]


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
