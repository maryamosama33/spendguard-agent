from spendguard.messages import savings_message
from spendguard.models import Expense
from spendguard.savings import overcharge, savings_summary


def _rejected(amount: float, deviation: float | None = None, duplicate_of: int | None = None,
              reason: str = "ارفض", decided_at: str = "2026-10-03") -> Expense:
    return Expense(amount=amount, status="rejected", price_deviation_pct=deviation,
                   duplicate_of=duplicate_of, rejection_reason=reason, decided_at=decided_at)


def test_overcharge_is_only_the_part_above_the_usual_price():
    assert overcharge(_rejected(18000.0, deviation=20.0)) == 3000.0
    assert overcharge(_rejected(18000.0, deviation=-5.0)) == 0.0


def test_summary_counts_duplicates_in_full_and_overpricing_by_overcharge():
    expenses = [
        _rejected(1220.0, deviation=1.7, duplicate_of=6),  # duplicate: whole amount
        _rejected(18000.0, deviation=20.0),  # flagged overprice: 3,000
        _rejected(860.0, deviation=4.5, reason="السعر عالي"),  # unflagged, rejected for price: 37
        _rejected(5000.0, deviation=4.0, reason="مش لمشروعنا"),  # not about money saved
        _rejected(9999.0, deviation=50.0, decided_at="2026-09-20"),  # another month
        Expense(amount=7000.0, status="approved", price_deviation_pct=30.0, decided_at="2026-10-01"),
    ]

    summary = savings_summary(expenses, "2026-10")

    assert summary["duplicates"] == {"count": 1, "amount": 1220.0}
    assert summary["overpricing"] == {"count": 2, "amount": 3037.03}
    assert summary["total_saved"] == 4257.03


def test_savings_message_in_egyptian_arabic():
    summary = {"month": "2026-10", "total_saved": 4220.0,
               "duplicates": {"count": 1, "amount": 1220.0}, "overpricing": {"count": 1, "amount": 3000.0}}

    assert savings_message(summary) == ("💰 في أكتوبر 2026 SpendGuard وفّرلك 4,220 جنيه: فاتورة مكررة واحدة اترفضت "
                                        "(1,220 جنيه) وزيادة سعر واحدة اترفضت (3,000 جنيه فرق سعر).")


def test_savings_message_plurals_and_nothing_saved():
    summary = {"month": "2026-09", "total_saved": 5000.0,
               "duplicates": {"count": 2, "amount": 2000.0}, "overpricing": {"count": 3, "amount": 3000.0}}

    assert "فاتورتين مكررين" in savings_message(summary)
    assert "3 زيادات في الأسعار" in savings_message(summary)
    assert savings_message(summary | {"total_saved": 0.0}) == "لسه مفيش توفير متسجل في سبتمبر 2026."
