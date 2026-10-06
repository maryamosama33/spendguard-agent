"""Ready-to-send Egyptian Arabic replies, built from tool results.

The chat model relays these verbatim, so the tone, dates and warnings are
the same on every run instead of depending on how the model phrases them.
"""

from pathlib import Path

from spendguard.learning import DEFAULT_THRESHOLD_PCT, Learning
from spendguard.models import Expense

ITEM_AR = {
    "steel rebar 12mm": "حديد تسليح 12 مم",
    "cement": "أسمنت",
    "sand": "رمل",
    "sand transport": "نقل رمل",
}

FIELD_AR = {
    "date": "التاريخ",
    "amount": "المبلغ",
    "supplier": "المورد",
    "project": "المشروع",
    "cost_item": "نوع المصروف (خامات/عمالة/نقل/مقاول باطن)",
    "requester": "مين طالب الصرف",
}


def _item(expense: Expense) -> str:
    item = expense.item or ""
    return ITEM_AR.get(item.strip().lower(), item)


def _money(amount: float | None) -> str:
    return f"{amount:,.0f} جنيه" if amount is not None else "مبلغ مش واضح"


def _what(expense: Expense) -> str:
    """'نقل رمل بـ 860 جنيه من النصر للنقل والتوريدات' (skips missing parts)."""
    parts = [_item(expense)]
    if expense.amount is not None:
        parts.append(f"بـ {_money(expense.amount)}")
    if expense.supplier:
        parts.append(f"من {expense.supplier}")
    return " ".join(p for p in parts if p)


def unreadable_document_message() -> str:
    return ("الصورة مش واضحة ومش قادر أقرا الفاتورة كويس. ممكن تصورها تاني في نور كويس "
            "والورقة كلها باينة ومفرودة؟")


def missing_fields_question(expense: Expense) -> str:
    missing = "، ".join(FIELD_AR.get(f, f) for f in expense.missing_fields)
    what = _what(expense)
    received = f"وصلني طلب {what}" if what else "وصلني طلبك"
    return f"تمام، {received}. بس ناقص: {missing}. ممكن تبعتهولي؟"


def _invoice_line(expense: Expense) -> str:
    parts = []
    if expense.invoice_number:
        parts.append(f"فاتورة {expense.invoice_number}")
    if expense.date:
        parts.append(f"بتاريخ {expense.date}")
    return " ".join(parts)


def _duplicate_warning(duplicate: Expense) -> str:
    status = {"approved": "معتمدة", "pending": "لسه مستنية موافقة",
              "rejected": f"اترفضت: {duplicate.rejection_reason}" if duplicate.rejection_reason else "اترفضت",
              }.get(duplicate.status, duplicate.status)
    ref = duplicate.invoice_number or f"طلب رقم {duplicate.id}"
    return f"⚠️ الفاتورة دي اتقدمت قبل كده: {ref} بتاريخ {duplicate.date} ({status})."


UNIT_AR = {"ton": "طن", "kg": "كيلو", "m3": "متر مكعب", "m2": "متر مربع", "meter": "متر",
           "bag": "شكارة", "trip": "نقلة", "piece": "قطعة", "day": "يوم"}


def _price_warning(anomaly: dict) -> str:
    times = min(anomaly["compared_count"], 3)
    pct, average = f"{anomaly['deviation_pct']:g}%", _money(anomaly["average_price"])
    if anomaly.get("basis") == "unit_price":
        unit = UNIT_AR.get((anomaly.get("unit") or "").lower(), "وحدة")
        warning = f"⚠️ سعر ال{unit} أعلى بـ {pct} من متوسط آخر {times} مرات ({average} لل{unit})."
    else:
        warning = f"⚠️ السعر أعلى بـ {pct} من متوسط آخر {times} مرات ({average})."
    return warning + _learned_threshold_note(anomaly)


def _cheaper_supplier_line(offer: dict) -> str:
    unit = UNIT_AR.get((offer.get("unit") or "").lower(), "وحدة")
    return (f"💡 مورد أرخص: {offer['supplier']} متوسط سعره {_money(offer['unit_price'])} لل{unit} "
            f"(أرخص بـ {offer['saving_pct']:g}%).")


def _learned_threshold_note(anomaly: dict) -> str:
    learned = anomaly.get("threshold_pct")
    if learned is None or learned == DEFAULT_THRESHOLD_PCT:
        return ""
    return f" (حد التنبيه {learned:g}% اتعلمته من قراراتك)"


def learning_message(expense: Expense, learning: Learning) -> str:
    """What the owner's decision just taught SpendGuard about this item."""
    item, limit = _item(expense) or "الصنف ده", f"{learning.threshold_pct:g}%"
    if learning.change == "stricter":
        seen = f"{learning.evidence[0]:g}%"
        return (f"💡 اتعلمت: رفضت {item} والزيادة كانت {seen} بس، "
                f"فمن دلوقتي هنبهك على {item} لو الزيادة فوق {limit}.")
    seen = " و".join(f"{d:g}%" for d in learning.evidence)
    return (f"💡 اتعلمت: وافقت على آخر زيادتين في {item} ({seen})، "
            f"فمن دلوقتي هنبهك على {item} بس لو الزيادة فوق {limit}.")


def _budget_warning(alert: dict) -> str:
    spent = f"{alert['spent']:,.0f} من {_money(alert['budget'])}"
    if alert["level"] == "over":
        return (f"⚠️ لو وافقت، مشروع {alert['project']} هيعدّي ميزانيته بـ {_money(-alert['remaining'])} "
                f"(هيوصل {spent}).")
    return (f"📊 لو وافقت، مشروع {alert['project']} هيكون صرف {alert['used_pct']:.0f}% من ميزانيته "
            f"({spent})، وفاضل {_money(alert['remaining'])}.")


def owner_approval_request(expense: Expense, duplicate: Expense | None, anomaly: dict,
                           source: str = "attach", budget: dict | None = None) -> str:
    """source: "attach" (Telegram: the gateway attaches the file), "note" (the
    terminal: say where it is saved) or "none" (sent separately)."""
    who = f" من {expense.requester}" if expense.requester else ""
    project = f" لمشروع {expense.project}" if expense.project else ""
    invoice = _invoice_line(expense)
    lines = [f"طلب صرف جديد رقم {expense.id}{who}: {_what(expense)}{project}"
             + (f"، {invoice}." if invoice else ".")]
    if duplicate is not None:
        lines.append(_duplicate_warning(duplicate))
    if anomaly.get("is_anomaly"):
        lines.append(_price_warning(anomaly))
    if anomaly.get("cheaper_supplier"):
        lines.append(_cheaper_supplier_line(anomaly["cheaper_supplier"]))
    if budget:
        lines.append(_budget_warning(budget))
    if expense.source_file and source == "attach":
        lines.append(_source_document_line(expense.source_file))
    elif expense.source_file and source == "note":
        lines.append(f"📎 المستند الأصلي محفوظ: data/documents/{Path(expense.source_file).name}")
    lines.append("موافق ولا مرفوض؟")
    return "\n".join(lines)


def forwarded_owner_request(expense: Expense, duplicate: Expense | None, anomaly: dict,
                            budget: dict | None = None) -> str:
    """The approval request pushed to the owner's own chat, without the file
    line (the file is attached to the message itself). A bare "موافق" is
    enough: the decision tools find the request (and ask if several wait)."""
    return owner_approval_request(expense, duplicate, anomaly, source="none", budget=budget)


def sent_to_owner_message(expense: Expense) -> str:
    return (f"تمام، طلبك رقم {expense.id} ({_what(expense)}) اتبعت لصاحب الشركة. "
            "هبلغك هنا أول ما يرد.")


def requester_decision_message(expense: Expense) -> str:
    if expense.status == "approved":
        return f"صاحب الشركة وافق على طلبك رقم {expense.id} ({_money(expense.amount)})."
    return f"صاحب الشركة رفض طلبك رقم {expense.id}. السبب: {expense.rejection_reason}"


def unclear_decision_message(expense_id: int) -> str:
    return (f"مش واضح إذا كنت موافق ولا رافض طلب رقم {expense_id}. "
            f"ابعت «موافق {expense_id}» أو «ارفض {expense_id}» والسبب.")


MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس",
             "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def _month_ar(month: str) -> str:
    year, number = month.split("-")
    return f"{MONTHS_AR[int(number) - 1]} {year}"


def _counted(n: int, one: str, two: str, few: str, many: str) -> str:
    """Arabic number agreement: فاتورة واحدة، فاتورتين، 3 فواتير، 11 فاتورة."""
    if n == 1:
        return one
    if n == 2:
        return two
    return f"{n} {few if n <= 10 else many}"


def savings_message(summary: dict) -> str:
    month = _month_ar(summary["month"])
    if not summary["total_saved"]:
        return f"لسه مفيش توفير متسجل في {month}."
    parts = []
    duplicates, overpricing = summary["duplicates"], summary["overpricing"]
    if duplicates["count"]:
        what = _counted(duplicates["count"], "فاتورة مكررة واحدة", "فاتورتين مكررين", "فواتير مكررة", "فاتورة مكررة")
        parts.append(f"{what} اترفضت ({_money(duplicates['amount'])})")
    if overpricing["count"]:
        what = _counted(overpricing["count"], "زيادة سعر واحدة", "زيادتين في الأسعار", "زيادات في الأسعار",
                        "زيادة في الأسعار")
        parts.append(f"{what} اترفضت ({_money(overpricing['amount'])} فرق سعر)")
    return f"💰 في {month} SpendGuard وفّرلك {_money(summary['total_saved'])}: " + " و".join(parts) + "."


def _budget_line(status: dict) -> str:
    line = (f"📊 مشروع {status['project']}: صرفنا {status['spent']:,.0f} من {_money(status['budget'])} "
            f"({status['used_pct']:.0f}%)")
    if status["remaining"] < 0:
        return line + f"، وعدّينا الميزانية بـ {_money(-status['remaining'])}."
    return line + f"، وفاضل {_money(status['remaining'])}."


def budget_message(statuses: list[dict], unknown_project: str | None = None) -> str:
    """Budget vs. actual, one line per project (F18)."""
    if unknown_project:
        return f"مفيش ميزانية متسجلة لمشروع {unknown_project}."
    if not statuses:
        return "مفيش ميزانيات مشاريع متسجلة."
    return "\n".join(_budget_line(s) for s in statuses)


def no_pending_message() -> str:
    return "مفيش طلبات مستنية موافقتك دلوقتي."


def already_decided_message(expense: Expense) -> str:
    decided = {"approved": "تم اعتماده", "rejected": "تم رفضه"}.get(expense.status, expense.status)
    return f"طلب رقم {expense.id} {decided} قبل كده."


def which_expense_message(pending_ids: list[int]) -> str:
    ids = "، ".join(str(i) for i in pending_ids)
    return f"فيه أكتر من طلب مستني موافقتك ({ids}). ابعت رقم الطلب، مثلاً: موافق {pending_ids[0]}"


def _source_document_line(path: str) -> str:
    """The MEDIA: tag makes the Hermes gateway attach the file itself (and hide
    the tag), so the owner sees the original invoice next to the request (F06)."""
    return f"📎 المستند الأصلي مرفق.\nMEDIA:{path}"


def approved_message(expense: Expense, sheet_synced: bool = True) -> str:
    where = "واتسجل في شيت المصاريف" if sheet_synced else "واتسجل في السيستم"
    return f"تمام، تم اعتماد طلب رقم {expense.id} ({_money(expense.amount)}) {where}."


def rejected_message(expense: Expense) -> str:
    return f"تمام، تم رفض طلب رقم {expense.id}. السبب: {expense.rejection_reason}"
