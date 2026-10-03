import pytest

from spendguard.decision import owner_decision


@pytest.mark.parametrize("message", [
    "موافق", "موافق، اعتمدها", "تمام اعتمد", "ماشي", "OK", "أوك موافقة", "تمام",
    "لا مانع", "مفيش مانع، اعتمده", "ok no problem", "yes approve it, no issues",
    "موافق، مفيش مشكلة", "موافق 12",
])
def test_approvals(message):
    assert owner_decision(message) == "approve"


@pytest.mark.parametrize("message", [
    "ارفضه، السعر عالي", "مرفوض", "لا", "لأ", "مش موافق", "غير موافق عشان مكرر",
    "don't approve", "dont approve", "do not approve", "I don’t approve this", "ارفض 12",
])
def test_rejections(message):
    assert owner_decision(message) == "reject"


@pytest.mark.parametrize("message", [
    "رسالة جديدة على الإيميل من sales@nsf-steel.example ومعاها فاتورة مرفقة",
    "ده لمشروع فيلات التجمع الخامس",  # contains "لا" inside a word, not as a word
    "",
    None,
    "ماشي بس لا تكررها",  # approves and says "no": ask again rather than guess
    "موافق لا",
    "مش مرفوض",
])
def test_no_decision(message):
    assert owner_decision(message) is None
