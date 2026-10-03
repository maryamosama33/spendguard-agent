"""Ready-to-send Egyptian Arabic replies, built from tool results.

The chat model relays these verbatim, so the tone, dates and warnings are
the same on every run instead of depending on how the model phrases them.
"""

from pathlib import Path

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
        return f"⚠️ سعر ال{unit} أعلى بـ {pct} من متوسط آخر {times} مرات ({average} لل{unit})."
    return f"⚠️ السعر أعلى بـ {pct} من متوسط آخر {times} مرات ({average})."


def owner_approval_request(expense: Expense, duplicate: Expense | None, anomaly: dict,
                           source: str = "attach") -> str:
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
    if expense.source_file and source == "attach":
        lines.append(_source_document_line(expense.source_file))
    elif expense.source_file and source == "note":
        lines.append(f"📎 المستند الأصلي محفوظ: data/documents/{Path(expense.source_file).name}")
    lines.append("موافق ولا مرفوض؟")
    return "\n".join(lines)


def forwarded_owner_request(expense: Expense, duplicate: Expense | None, anomaly: dict) -> str:
    """The approval request pushed to the owner's own chat: that chat has no
    context, so the owner replies with the request number."""
    return (owner_approval_request(expense, duplicate, anomaly, source="none")
            + f"\nرد بـ «موافق {expense.id}» أو «ارفض {expense.id}» والسبب.")


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


def no_pending_message() -> str:
    return "مفيش طلبات مستنية موافقتك دلوقتي."


def already_decided_message(expense: Expense) -> str:
    decided = {"approved": "اتعتمد", "rejected": "اترفض"}.get(expense.status, expense.status)
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
    return f"تمام، اتعتمد طلب رقم {expense.id} ({_money(expense.amount)}) {where}."


def rejected_message(expense: Expense) -> str:
    return f"تمام، اترفض طلب رقم {expense.id}. السبب: {expense.rejection_reason}"
