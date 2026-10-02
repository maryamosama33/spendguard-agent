from spendguard.messages import (
    approved_message,
    missing_fields_question,
    owner_approval_request,
    rejected_message,
)
from spendguard.models import Expense

NO_ANOMALY = {"is_anomaly": False, "average_price": None, "deviation_pct": None, "compared_count": 0}


def _steel(**overrides) -> Expense:
    base = dict(
        id=57, date="2026-10-01", amount=18000.0, supplier="مصنع الحديد الوطني",
        project="فيلات التجمع الخامس", item="steel rebar 12mm", requester="محمد حسن",
        invoice_number="NSF-2241",
    )
    base.update(overrides)
    return Expense(**base)


def test_approval_request_has_details_and_question():
    msg = owner_approval_request(_steel(), None, NO_ANOMALY)

    assert msg == (
        "طلب صرف جديد رقم 57 من محمد حسن: حديد تسليح 12 مم بـ 18,000 جنيه من مصنع الحديد الوطني"
        " لمشروع فيلات التجمع الخامس، فاتورة NSF-2241 بتاريخ 2026-10-01.\n"
        "موافق ولا مرفوض؟"
    )


def test_approval_request_includes_price_warning():
    anomaly = {"is_anomaly": True, "average_price": 15000.0, "deviation_pct": 20.0, "compared_count": 3}

    msg = owner_approval_request(_steel(), None, anomaly)

    assert "⚠️ السعر أعلى بـ 20% من متوسط آخر 3 مرات (15,000 جنيه)." in msg
    assert msg.endswith("موافق ولا مرفوض؟")


def test_approval_request_includes_duplicate_warning():
    earlier = _steel(id=6, invoice_number="SC-1140", date="2026-09-10", status="approved")

    msg = owner_approval_request(_steel(), earlier, NO_ANOMALY)

    assert "⚠️ الفاتورة دي اتقدمت قبل كده: SC-1140 بتاريخ 2026-09-10 (معتمدة)." in msg


def test_missing_fields_question_names_fields_in_arabic():
    sand = Expense(amount=860.0, supplier="النصر للنقل والتوريدات", item="sand transport",
                   missing_fields=["project"])

    msg = missing_fields_question(sand)

    assert msg == "تمام، وصلني طلب نقل رمل بـ 860 جنيه من النصر للنقل والتوريدات. بس ناقص: المشروع. ممكن تبعتهولي؟"


def test_missing_fields_question_when_nothing_readable():
    msg = missing_fields_question(Expense(missing_fields=["amount", "project"]))

    assert msg == "تمام، وصلني طلبك. بس ناقص: المبلغ، المشروع. ممكن تبعتهولي؟"


def test_unknown_item_name_is_kept_as_is():
    msg = owner_approval_request(_steel(item="بلاط"), None, NO_ANOMALY)

    assert "بلاط بـ 18,000 جنيه" in msg


def test_approved_without_sheets_does_not_claim_sheet_row():
    assert approved_message(_steel(), sheet_synced=False) == "تمام، اتعتمد طلب رقم 57 (18,000 جنيه) واتسجل في السيستم."


def test_approved_and_rejected_messages():
    assert approved_message(_steel()) == "تمام، اتعتمد طلب رقم 57 (18,000 جنيه) واتسجل في شيت المصاريف."
    assert rejected_message(_steel(rejection_reason="السعر عالي")) == "تمام، اترفض طلب رقم 57. السبب: السعر عالي"
