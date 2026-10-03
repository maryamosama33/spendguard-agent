import pytest

from spendguard.normalize import canonical_name, resolve_name

PROJECTS = ["فيلات التجمع الخامس", "مستودع 6 أكتوبر", "موقع العاصمة الإدارية"]


def test_mistyped_project_snaps_to_known():
    assert canonical_name("فيلا التجمع الخامس", PROJECTS) == "فيلات التجمع الخامس"


def test_exact_name_unchanged():
    assert canonical_name("مستودع 6 أكتوبر", PROJECTS) == "مستودع 6 أكتوبر"


def test_genuinely_new_name_kept():
    assert canonical_name("برج المعادي", PROJECTS) == "برج المعادي"


def test_missing_name_stays_missing():
    assert canonical_name(None, PROJECTS) is None
    assert canonical_name("", PROJECTS) == ""


def test_similar_but_different_supplier_not_merged():
    suppliers = ["مصنع الحديد الوطني"]

    assert canonical_name("مصنع الأسمنت الوطني", suppliers) == "مصنع الأسمنت الوطني"


@pytest.mark.parametrize("asked, known", [
    ("فيلا التجمع الخامس", "فيلات التجمع الخامس"),
    ("التجمع الخامس", "فيلات التجمع الخامس"),
    ("فيلا التجمع", "فيلات التجمع الخامس"),
    ("العاصمة", "موقع العاصمة الإدارية"),
    ("6 اكتوبر", "مستودع 6 أكتوبر"),
])
def test_question_short_forms_resolve(asked, known):
    assert resolve_name(asked, PROJECTS) == known


def test_short_form_matching_two_names_is_left_alone():
    assert resolve_name("مصنع", ["مصنع الحديد الوطني", "مصنع الأسمنت"]) == "مصنع"


def test_resolve_unknown_or_missing_name_unchanged():
    assert resolve_name("برج المعادي", PROJECTS) == "برج المعادي"
    assert resolve_name(None, PROJECTS) is None
