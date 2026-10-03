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


_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})


def _words(text: str) -> list[str]:
    return text.lower().translate(_ALEF).split()


def _is_short_form(short: str, full: str) -> bool:
    """Every word of `short` starts a word of `full`: "فيلا التجمع" and
    "التجمع الخامس" are short forms of "فيلات التجمع الخامس"."""
    full_words = _words(full)
    return all(any(fw.startswith(w) for fw in full_words) for w in _words(short))


def resolve_name(name: str | None, known: Iterable[str]) -> str | None:
    """For questions, which name names often only in part: a close match, else
    the one known name it is a short form of, else `name` unchanged."""
    known = list(known)
    close = canonical_name(name, known)
    if not name or close != name or name in known:
        return close
    candidates = [k for k in known if _is_short_form(name, k)]
    return candidates[0] if len(candidates) == 1 else name
