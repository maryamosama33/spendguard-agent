from spendguard.learning import DEFAULT_THRESHOLD_PCT, learn_threshold, rejected_for_price
from spendguard.models import Expense

_ids = iter(range(1, 1000))


def _decided(status: str, deviation: float | None, item: str = "sand transport",
             reason: str | None = None) -> Expense:
    return Expense(id=next(_ids), item=item, status=status, price_deviation_pct=deviation,
                   rejection_reason=reason)


def test_no_decisions_means_default_threshold():
    learning = learn_threshold("sand transport", [])

    assert (learning.threshold_pct, learning.change) == (DEFAULT_THRESHOLD_PCT, None)


def test_price_rejection_of_unflagged_increase_makes_item_stricter():
    learning = learn_threshold("sand transport", [_decided("rejected", 4.5, reason="السعر عالي")])

    assert (learning.threshold_pct, learning.change, learning.evidence) == (3.0, "stricter", [4.5])


def test_rejection_for_another_reason_teaches_nothing():
    learning = learn_threshold("sand transport", [_decided("rejected", 4.5, reason="مكررة")])

    assert learning.threshold_pct == DEFAULT_THRESHOLD_PCT


def test_two_approved_warnings_in_a_row_make_item_quieter():
    decided = [_decided("approved", 20.0, item="steel"), _decided("approved", 18.0, item="steel")]

    learning = learn_threshold("steel", decided)

    assert (learning.threshold_pct, learning.change, learning.evidence) == (25.0, "quieter", [20.0, 18.0])


def test_rejected_warning_in_between_resets_the_approval_streak():
    decided = [_decided("approved", 20.0, item="steel"),
               _decided("rejected", 22.0, item="steel", reason="غالي"),
               _decided("approved", 19.0, item="steel")]

    assert learn_threshold("steel", decided).threshold_pct == DEFAULT_THRESHOLD_PCT


def test_unflagged_approvals_and_other_items_do_not_count():
    decided = [_decided("approved", 5.0, item="steel"), _decided("approved", 20.0, item="cement"),
               _decided("approved", 20.0, item="steel")]

    assert learn_threshold("steel", decided).threshold_pct == DEFAULT_THRESHOLD_PCT


def test_later_price_rejection_overrides_a_relaxed_threshold():
    decided = [_decided("approved", 20.0, item="steel"), _decided("approved", 21.0, item="steel"),
               _decided("rejected", 18.0, item="steel", reason="السعر مرتفع")]

    assert learn_threshold("steel", decided).threshold_pct == 17.0


def test_price_reason_recognised_in_arabic_and_english():
    assert rejected_for_price(Expense(status="rejected", rejection_reason="ارفضه، السعر عالى"))
    assert rejected_for_price(Expense(status="rejected", rejection_reason="too expensive"))
    assert not rejected_for_price(Expense(status="rejected", rejection_reason="ارفض"))
