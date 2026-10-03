"""Learn each item's price-warning threshold from the owner's decisions.

The price baseline already follows approved prices, so this learns what the
baseline can't: how sensitive the owner is about each item.

- Stricter: the owner rejects, because of the price, an increase SpendGuard
  didn't flag ("ارفض، السعر عالي" at +4.5%). That item is flagged from just
  below that level on.
- Quieter: the owner approves 2 flagged increases in a row for an item. That
  item is flagged only above them.

Thresholds are recomputed by replaying the decisions in order, so nothing is
stored, a restart changes nothing, and a reset forgets everything.
"""

import math
import re
from dataclasses import dataclass, field

from spendguard.checks import PRICE_ANOMALY_THRESHOLD
from spendguard.models import Expense

DEFAULT_THRESHOLD_PCT = PRICE_ANOMALY_THRESHOLD * 100
MAX_THRESHOLD_PCT = 50.0
APPROVALS_TO_RELAX = 2
PRICE_WORDS = {"سعر", "السعر", "غالي", "غاليه", "عالي", "عاليه", "مرتفع", "زياده", "كتير",
               "price", "expensive", "overpriced", "high"}
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})


@dataclass
class Learning:
    threshold_pct: float = DEFAULT_THRESHOLD_PCT
    change: str | None = None  # "stricter" | "quieter": the last rule that moved the threshold
    evidence: list[float] = field(default_factory=list)  # the deviations behind that change


def rejected_for_price(expense: Expense) -> bool:
    text = (expense.rejection_reason or "").lower().translate(_ALEF)
    return expense.status == "rejected" and any(w in PRICE_WORDS for w in re.findall(r"\w+", text))


def _same_item(expense: Expense, item: str) -> bool:
    return (expense.item or "").strip().lower() == item.strip().lower()


def _stricter(deviation: float) -> float:
    return float(max(0, math.floor(deviation) - 1))


def _quieter(deviations: list[float]) -> float:
    """The next multiple of 5 above the approved increases: 20 -> 25."""
    return min(MAX_THRESHOLD_PCT, math.floor(max(deviations) / 5) * 5 + 5.0)


def learn_threshold(item: str | None, decided: list[Expense]) -> Learning:
    """Replay the owner's decisions on this item, oldest first."""
    learning = Learning()
    if not item:
        return learning
    approved_flags: list[float] = []
    for expense in sorted((e for e in decided if _same_item(e, item)), key=lambda e: e.id or 0):
        deviation = expense.price_deviation_pct
        if deviation is None:
            continue
        flagged = deviation > learning.threshold_pct
        if expense.status == "approved" and flagged:
            approved_flags.append(deviation)
            if len(approved_flags) == APPROVALS_TO_RELAX:
                learning = Learning(_quieter(approved_flags), "quieter", approved_flags)
                approved_flags = []
        elif rejected_for_price(expense) and not flagged and deviation > 0:
            learning = Learning(_stricter(deviation), "stricter", [deviation])
            approved_flags = []
        elif rejected_for_price(expense):
            approved_flags = []  # the owner agreed with the warning
    return learning
