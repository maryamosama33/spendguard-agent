"""Read the owner's explicit approve/reject decision from their reply.

approve_expense / reject_expense require the owner's own words, so an expense
is never decided by the chat model's judgment alone. A rejection can't be
undone, so a message that says both ("ماشي بس لا تكررها") is no decision:
the owner is asked again.
"""

import re

APPROVE_WORDS = {"موافق", "موافقه", "اعتمد", "اعتمدها", "اعتمده", "ماشي", "تمام", "اوكي",
                 "ok", "okay", "yes", "approve", "approved"}
REJECT_WORDS = {"ارفض", "ارفضه", "ارفضها", "مرفوض", "مرفوضه", "رفض", "لا",
                "no", "reject", "rejected"}
NEGATIONS = {"مش", "غير", "not", "dont"}
# Phrases built from a "no" word that mean "go ahead" (matched after normalizing).
APPROVE_PHRASES = ["لا مانع", "مفيش مانع", "no problem", "no problems", "no issue",
                   "no issues", "no objection"]

_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه"})


def _normalize(text: str) -> str:
    text = re.sub(r"[ً-ْ]", "", text.lower()).translate(_ALEF)  # drop diacritics
    text = re.sub(r"n['’]t\b", " not", text)  # don't -> do not
    for phrase in APPROVE_PHRASES:
        text = re.sub(rf"\b{phrase}\b", " ok ", text)
    return text


def _signals(words: list[str]) -> tuple[bool, bool]:
    """(approves, rejects). "مش موافق" rejects; "مش مرفوض" is neither."""
    approves = rejects = False
    i = 0
    while i < len(words):
        word, following = words[i], words[i + 1] if i + 1 < len(words) else ""
        if word in NEGATIONS and following in APPROVE_WORDS:
            rejects, i = True, i + 2
            continue
        if word in NEGATIONS and following in REJECT_WORDS:
            i += 2
            continue
        approves |= word in APPROVE_WORDS
        rejects |= word in REJECT_WORDS
        i += 1
    return approves, rejects


def owner_decision(message: str | None) -> str | None:
    """"approve", "reject", or None when the message holds no clear decision."""
    approves, rejects = _signals(re.findall(r"\w+", _normalize(message or "")))
    if approves == rejects:
        return None
    return "approve" if approves else "reject"
