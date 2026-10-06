from spendguard.messages import expense_history_message
from spendguard.models import Expense

PENDING = Expense(id=13, amount=18000.0, item="cement", supplier="شركة أسمنت السويس",
                  project="مستودع 6 أكتوبر", requester="أحمد علي", source_channel="telegram",
                  sender="555", created_at="2026-10-06 14:30")


def test_history_of_a_pending_request():
    assert expense_history_message(PENDING, 13) == (
        "🧾 طلب رقم 13: أسمنت بـ 18,000 جنيه من شركة أسمنت السويس لمشروع مستودع 6 أكتوبر.\n"
        "• اتقدم في 2026-10-06 14:30 من أحمد علي عن طريق تليجرام (555).\n"
        "• لسه مستني موافقة صاحب الشركة.")


def test_history_names_who_approved_and_when():
    approved = PENDING.model_copy(update={"status": "approved", "decided_by": "1386120774",
                                          "decided_at": "2026-10-06 15:02"})

    assert expense_history_message(approved, 13).endswith(
        "• اتعتمد من صاحب الشركة (تليجرام 1386120774) في 2026-10-06 15:02.")


def test_history_of_a_rejected_duplicate_gives_reason():
    rejected = PENDING.model_copy(update={"status": "rejected", "decided_by": "owner", "duplicate_of": 6,
                                          "decided_at": "2026-10-06 15:05", "rejection_reason": "مكررة"})
    message = expense_history_message(rejected, 13)

    assert "• ⚠️ كان مكرر من طلب رقم 6." in message
    assert message.endswith("• اترفض من صاحب الشركة في 2026-10-06 15:05. السبب: مكررة")


def test_history_of_unknown_request():
    assert expense_history_message(None, 99) == "مفيش طلب برقم 99."
