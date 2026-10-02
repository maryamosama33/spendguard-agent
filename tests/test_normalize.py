from spendguard.normalize import canonical_name

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
