"""Read the owner's explicit approve/reject decision from their reply.

approve_expense / reject_expense require the owner's own words, so an expense
is never decided by the chat model's judgment alone.
"""

import re

APPROVE_WORDS = {"موافق", "موافقه", "اعتمد", "اعتمدها", "اعتمده", "ماشي",
                 "ok", "okay", "yes", "approve", "approved"}
REJECT_WORDS = {"ارفض", "ارفضه", "ارفضها", "مرفوض", "مرفوضه", "رفض", "لا",
                "no", "reject", "rejected"}
NEGATIONS = {"مش", "غير", "not"}

_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه"})


def _words(text: str) -> list[str]:
    text = re.sub(r"[ً-ْ]", "", text.lower()).translate(_ALEF)  # drop diacritics
    return re.findall(r"\w+", text)


def owner_decision(message: str | None) -> str | None:
    """"approve", "reject", or None when the message holds no explicit decision."""
    words = _words(message or "")
    negated_approval = any(a in NEGATIONS and b in APPROVE_WORDS for a, b in zip(words, words[1:]))
    if negated_approval or any(w in REJECT_WORDS for w in words):
        return "reject"
    if any(w in APPROVE_WORDS for w in words):
        return "approve"
    return None
