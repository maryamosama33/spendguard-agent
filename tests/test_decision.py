import pytest

from spendguard.decision import owner_decision


@pytest.mark.parametrize("message", ["موافق", "موافق، اعتمدها", "تمام اعتمد", "ماشي", "OK", "أوك موافقة"])
def test_approvals(message):
    assert owner_decision(message) == "approve"


@pytest.mark.parametrize("message", ["ارفضه، السعر عالي", "مرفوض", "لا", "مش موافق", "غير موافق عشان مكرر"])
def test_rejections(message):
    assert owner_decision(message) == "reject"


@pytest.mark.parametrize("message", [
    "رسالة جديدة على الإيميل من sales@nsf-steel.example ومعاها فاتورة مرفقة",
    "ده لمشروع فيلات التجمع الخامس",  # contains "لا" inside a word, not as a word
    "",
    None,
])
def test_no_decision(message):
    assert owner_decision(message) is None
