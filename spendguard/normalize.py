"""Snap slightly-mistyped names back to known ones.

The chat model retypes values between tools (e.g. "فيلا التجمع الخامس" for
"فيلات التجمع الخامس"). Exact-match logic downstream (price history per
supplier, spending per project) would then miss them.
"""

import difflib
from collections.abc import Iterable

MATCH_CUTOFF = 0.85


def canonical_name(name: str | None, known: Iterable[str], cutoff: float = MATCH_CUTOFF) -> str | None:
    """The known name `name` most likely means, or `name` unchanged if none is close."""
    if not name:
        return name
    known = list(known)
    if name in known:
        return name
    matches = difflib.get_close_matches(name.strip(), known, n=1, cutoff=cutoff)
    return matches[0] if matches else name
