from pathlib import Path
from unittest.mock import patch

import pytest
from google.genai import errors as genai_errors

from spendguard import server, storage
from spendguard.extraction import ExtractionFailed
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


def test_save_expense_archives_source_document_and_attaches_it(seeded_db, tmp_path, monkeypatch, telegram):
    monkeypatch.setattr(storage, "DOCUMENTS_DIR", tmp_path / "documents")
    original = tmp_path / "hermes" / "cache" / "documents" / "doc_ab12_invoice.PDF"  # a Telegram upload
    original.parent.mkdir(parents=True)
    original.write_bytes(b"%PDF-1.4 steel")

    result = server.save_expense(STEEL | {"source_file": str(original)}, telegram_sender=OWNER)

    archived = Path(result["source_file"])
    assert archived.parent == storage.DOCUMENTS_DIR
    assert archived.read_bytes() == b"%PDF-1.4 steel"
    assert archived.suffix == ".pdf"
    assert f"MEDIA:{result['source_file']}" in result["reply_to_owner"]
    original.unlink()  # the gateway cache is cleaned; the archived copy remains
    assert archived.exists()


def test_terminal_reply_names_saved_document_instead_of_media_tag(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DOCUMENTS_DIR", tmp_path / "documents")
    invoice = storage.SEED_DIR / "invoices" / "steel_invoice_overpriced.pdf"

    result = server.save_expense(STEEL | {"source_file": str(invoice)})

    assert "MEDIA:" not in result["reply_to_owner"]
    assert f"📎 المستند الأصلي محفوظ: data/documents/{Path(result['source_file']).name}" in result["reply_to_owner"]


def test_archive_document_is_idempotent_and_skips_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DOCUMENTS_DIR", tmp_path / "documents")
    monkeypatch.setattr(storage, "SEED_DIR", tmp_path / "seed")
    original = tmp_path / "seed" / "photo.jpg"
    original.parent.mkdir()
    original.write_bytes(b"jpeg")

    first = storage.archive_document(str(original))

    assert storage.archive_document(first) == first
    assert len(list(storage.DOCUMENTS_DIR.iterdir())) == 1
    assert storage.archive_document(str(tmp_path / "seed" / "gone.jpg")) is None
    assert storage.archive_document(None) is None


def test_model_supplied_path_outside_upload_folders_is_not_attached(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DOCUMENTS_DIR", tmp_path / "documents")
    secret = tmp_path / "Desktop" / "salaries.pdf"
    secret.parent.mkdir()
    secret.write_bytes(b"%PDF private")

    result = server.save_expense(STEEL | {"source_file": str(secret)})

    assert result["source_file"] is None
    assert "MEDIA:" not in result["reply_to_owner"]
    assert not storage.DOCUMENTS_DIR.exists()


def test_extract_refuses_files_outside_upload_folders(tmp_path):
    env_like = tmp_path / "credentials.json"
    env_like.write_text("{}")
    other_pdf = tmp_path / "private.pdf"
    other_pdf.write_bytes(b"%PDF")

    with patch("spendguard.server._extract_expense") as gemini:
        for path in (env_like, other_pdf):
            assert server.extract_expense(str(path), "telegram", "1")["retryable"] is False

    gemini.assert_not_called()


def test_extract_reads_demo_invoices_in_seed_folder():
    invoice = storage.SEED_DIR / "invoices" / "steel_invoice_overpriced.pdf"

    with patch("spendguard.server._extract_expense", return_value=Expense(amount=1.0, supplier="x",
                                                                         confidence=0.9)) as gemini, \
         patch("spendguard.server._known_items", return_value=[]):
        server.extract_expense(str(invoice), "email", "a@b.example")

    gemini.assert_called_once()


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
    assert result["reply"] == f"تمام، تم رفض طلب رقم {saved['id']}. السبب: السعر عالي"


def test_owner_approval_writes_sheet_once(seeded_db):
    saved = server.save_expense(STEEL)

    with patch("spendguard.server.append_to_sheet") as sheet:
        first = server.approve_expense(saved["id"], owner_message="موافق")
        second = server.approve_expense(saved["id"], owner_message="موافق")

    assert first["status"] == "approved"
    assert second["decided"] is False
    sheet.assert_called_once()


def test_repeat_decision_naming_the_request_says_already_decided(seeded_db):
    saved = server.save_expense(STEEL)
    with patch("spendguard.server.append_to_sheet", return_value=False):
        server.approve_expense(saved["id"], f"موافق {saved['id']}")

    again = server.approve_expense(saved["id"], f"موافق {saved['id']}")

    assert again["reply"] == f"طلب رقم {saved['id']} تم اعتماده قبل كده."


def test_owner_approves_without_number_when_one_request_pending(seeded_db):
    # The request was pushed to the owner's chat, so the model there knows no id.
    saved = server.save_expense(STEEL)

    with patch("spendguard.server.append_to_sheet", return_value=False):
        result = server.approve_expense(owner_message="موافق")

    assert result["id"] == saved["id"] and result["status"] == "approved"


def test_invented_id_is_ignored_for_the_only_pending_request(seeded_db):
    saved = server.save_expense(STEEL)

    result = server.reject_expense(expense_id=1, owner_message="ارفضه")  # 1 is an approved seed row

    assert result["id"] == saved["id"] and result["status"] == "rejected"
    assert result["rejection_reason"] == "ارفضه"


def test_decision_with_nothing_pending_says_so(seeded_db):
    result = server.approve_expense(owner_message="موافق")

    assert result["decided"] is False
    assert result["reply"] == "مفيش طلبات مستنية موافقتك دلوقتي."


def test_approve_refused_when_owner_said_no(seeded_db):
    saved = server.save_expense(STEEL)

    with patch("spendguard.server.append_to_sheet") as sheet:
        result = server.approve_expense(saved["id"], owner_message="مش موافق")

    assert result["decided"] is False
    sheet.assert_not_called()


def test_seeded_steel_price_compared_per_ton(seeded_db):
    result = server.save_expense(STEEL | {"quantity": 1, "unit": "ton"})

    assert "⚠️ سعر الطن أعلى بـ 20%" in result["reply_to_owner"]


def test_overpriced_steel_request_suggests_the_cheaper_seeded_supplier(seeded_db):
    result = server.save_expense(STEEL | {"quantity": 1, "unit": "ton"})

    assert ("💡 مورد أرخص: مجموعة حديد الدلتا متوسط سعره 15,267 جنيه للطن (أرخص بـ 15.2%)."
            in result["reply_to_owner"])
    assert result["reply_to_owner"].endswith("موافق ولا مرفوض؟")


def test_two_tons_at_the_usual_price_raise_no_warning(seeded_db):
    result = server.save_expense(STEEL | {"quantity": 2, "unit": "ton", "amount": 30000.0})

    assert "أعلى بـ" not in result["reply_to_owner"]  # no price warning (the budget one is fine)


def test_query_with_short_project_name_finds_its_spending(seeded_db):
    result = server.query_expenses(project="التجمع الخامس")

    assert result["filters"]["project"] == "فيلات التجمع الخامس"
    assert result["count"] > 0


SAND = dict(
    date="2026-10-01", amount=860.0, quantity=1, unit="trip", supplier="النصر للنقل والتوريدات",
    project="فيلات التجمع الخامس", cost_item="transport", item="sand transport", requester="محمد حسن",
)


def test_owner_rejecting_a_small_increase_for_price_makes_spendguard_stricter(seeded_db):
    first = server.save_expense(SAND | {"invoice_number": "NT-401"})
    assert "⚠️" not in first["reply_to_owner"]  # +4.5%: under the default 15%

    result = server.reject_expense(owner_message="ارفض، السعر عالي")

    assert result["reply"].endswith("💡 اتعلمت: رفضت نقل رمل والزيادة كانت 4.5% بس، "
                                    "فمن دلوقتي هنبهك على نقل رمل لو الزيادة فوق 3%.")
    again = server.save_expense(SAND | {"invoice_number": "NT-402", "date": "2026-10-02"})
    assert "⚠️ سعر النقلة أعلى بـ 4.5%" in again["reply_to_owner"]
    assert "(حد التنبيه 3% اتعلمته من قراراتك)" in again["reply_to_owner"]


def test_owner_approving_two_increases_makes_spendguard_quieter(seeded_db):
    steel = STEEL | {"quantity": 1, "unit": "ton"}
    with patch("spendguard.server.append_to_sheet", return_value=False):
        server.save_expense(steel)
        first = server.approve_expense(owner_message="موافق")
        server.save_expense(steel | {"invoice_number": "NSF-2250", "date": "2026-10-02", "amount": 19300.0})
        second = server.approve_expense(owner_message="موافق")

    assert "💡" not in first["reply"]
    assert "💡 اتعلمت: وافقت على آخر زيادتين في حديد تسليح 12 مم (20% و20.1%)" in second["reply"]
    assert "فوق 25%" in second["reply"]


def test_rejection_without_a_price_reason_teaches_nothing(seeded_db):
    server.save_expense(SAND | {"invoice_number": "NT-401"})

    result = server.reject_expense(owner_message="ارفض، مش لمشروعنا")

    assert "💡" not in result["reply"]


CEMENT_AGAIN = dict(
    date="2026-09-10", amount=1220.0, quantity=1, unit="ton", supplier="شركة أسمنت السويس",
    project="مستودع 6 أكتوبر", cost_item="materials", item="cement", requester="أحمد علي",
    invoice_number="SC-1140",
)


def test_savings_report_after_rejecting_a_duplicate_and_an_overprice(seeded_db):
    with patch("spendguard.server.append_to_sheet", return_value=False):
        cement = server.save_expense(CEMENT_AGAIN)
        server.reject_expense(cement["id"], "مكررة", f"ارفض {cement['id']} مكررة")
        steel = server.save_expense(STEEL | {"quantity": 1, "unit": "ton"})
        server.reject_expense(steel["id"], "السعر عالي", f"ارفض {steel['id']} السعر عالي")

    report = server.savings_report()

    assert cement["duplicate_of"] is not None
    assert report["total_saved"] == 4220.0  # 1,220 duplicate + 3,000 overcharge on 18,000 at +20%
    assert "SpendGuard وفّرلك 4,220 جنيه" in report["reply"]


def test_savings_report_for_a_month_without_rejections(seeded_db):
    assert server.savings_report("2026-01")["reply"] == "لسه مفيش توفير متسجل في يناير 2026."


def test_seeded_steel_request_warns_project_nears_its_budget(seeded_db):
    result = server.save_expense(STEEL | {"quantity": 1, "unit": "ton"})

    assert result["budget_alert"]["level"] == "near"
    assert "📊 لو وافقت، مشروع فيلات التجمع الخامس هيكون صرف 87% من ميزانيته" in result["reply_to_owner"]


def test_budget_report_counts_approved_spend_once_owner_approves(seeded_db):
    with patch("spendguard.server.append_to_sheet", return_value=False):
        steel = server.save_expense(STEEL | {"quantity": 1, "unit": "ton"})
        before = server.budget_report("التجمع الخامس")["projects"][0]["spent"]
        server.approve_expense(steel["id"], f"موافق {steel['id']}")

    report = server.budget_report("التجمع الخامس")

    assert (before, report["projects"][0]["spent"]) == (47470.0, 65470.0)
    assert report["reply"].startswith("📊 مشروع فيلات التجمع الخامس: صرفنا 65,470 من 75,000 جنيه")


def test_budget_report_all_projects_and_unknown_project(seeded_db):
    assert len(server.budget_report()["projects"]) == 3
    assert server.budget_report("مول العرب")["reply"] == "مفيش ميزانية متسجلة لمشروع مول العرب."


def test_expense_history_records_terminal_request_and_owner_decision(seeded_db):
    with patch("spendguard.server.append_to_sheet", return_value=False):
        steel = server.save_expense(STEEL)
        server.reject_expense(steel["id"], "السعر عالي", f"ارفض {steel['id']} السعر عالي")

    history = server.expense_history(steel["id"])

    assert history["created_at"] and history["decided_at"] and history["decided_by"] == "owner"
    assert "• اترفض من صاحب الشركة في " in history["reply"]
    assert server.expense_history(999)["reply"] == "مفيش طلب برقم 999."


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
    assert text.endswith("موافق ولا مرفوض؟") and "⚠️ السعر أعلى" in text
    assert f"رقم {result['id']}" in text  # the number is still in the request's first line
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


def test_audit_trail_records_engineer_sender_and_owner_who_approved(seeded_db, telegram):
    saved = server.save_expense(STEEL, telegram_sender=ENGINEER)
    with patch("spendguard.server.append_to_sheet", return_value=False):
        server.approve_expense(saved["id"], f"موافق {saved['id']}", telegram_sender=OWNER)

    reply = server.expense_history(saved["id"])["reply"]

    assert "عن طريق تليجرام (555)" in reply
    assert f"• اتعتمد من صاحب الشركة (تليجرام {OWNER}) في " in reply


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


def test_save_expense_reply_warns_on_invoice_resubmitted_by_someone_else(seeded_db):
    server.save_expense(STEEL | {"sender": "201001112222"})

    result = server.save_expense(STEEL | {"sender": "201003334444", "requester": "أحمد علي"})

    assert "⚠️ الفاتورة دي اتقدمت قبل كده: NSF-2241" in result["reply_to_owner"]


def test_retried_save_reuses_the_pending_row_without_a_duplicate_warning(seeded_db):
    first = server.save_expense(STEEL)

    retry = server.save_expense(STEEL)

    assert retry["id"] == first["id"] and retry["already_saved"] is True
    assert "اتقدمت قبل كده" not in retry["reply_to_owner"]
    assert len(storage.list_expenses(storage.get_connection(), statuses=["pending"])) == 1


def test_retried_engineer_save_does_not_message_owner_twice(seeded_db, telegram):
    server.save_expense(STEEL, telegram_sender=ENGINEER)

    retry = server.save_expense(STEEL, telegram_sender=ENGINEER)

    assert len(telegram["owners"]) == 1
    assert "اتبعت لصاحب الشركة" in retry["reply_to_sender"]


def _api_error(code: int) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"code": code, "message": "x"}})


def test_extract_expense_quota_error_is_retryable_dict():
    with patch("spendguard.server._extract_expense", side_effect=_api_error(429)), \
         patch("spendguard.server._document_refusal", return_value=None):
        result = server.extract_expense("invoice.pdf", "email", "a@b.example")

    assert result["retryable"] is True
    assert "429" in result["error"]


def test_extract_expense_bad_request_is_not_retryable():
    with patch("spendguard.server._extract_expense", side_effect=_api_error(400)), \
         patch("spendguard.server._document_refusal", return_value=None):
        result = server.extract_expense("invoice.pdf", "email", "a@b.example")

    assert result["retryable"] is False


def test_unparsable_gemini_answer_is_retryable_error_dict():
    with patch("spendguard.server._extract_expense_from_text", side_effect=ExtractionFailed("bad json")):
        result = server.extract_expense_from_text("دفعت 3000 جنيه", "telegram", "201")

    assert result["retryable"] is True and "error" in result


def test_unsupported_file_type_is_error_dict_not_exception():
    with patch("spendguard.server._extract_expense", side_effect=ValueError("Could not determine MIME type")), \
         patch("spendguard.server._document_refusal", return_value=None):
        result = server.extract_expense("invoice.xyz", "telegram", "201")

    assert result["retryable"] is False and "MIME" in result["error"]


def test_extract_expense_from_text_quota_error_is_retryable_dict():
    with patch("spendguard.server._extract_expense_from_text", side_effect=_api_error(503)):
        result = server.extract_expense_from_text("دفعت 3000 جنيه", "whatsapp", "201")

    assert result["retryable"] is True


def _extract_with(**fields):
    expense = Expense(**fields)
    with patch("spendguard.server._extract_expense", return_value=expense), \
         patch("spendguard.server._document_refusal", return_value=None), \
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
