"""Pure text and value normalization helpers (no DB, no IO)."""

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """NFC-normalize, canonicalize curly apostrophes to ASCII, collapse whitespace.

    Canonicalizing the curly apostrophe (U+2019) is load-bearing: otherwise
    "Bachelor's Degree" written with a curly quote and one with a straight quote
    would count as two different education levels.
    """
    text = unicodedata.normalize("NFC", value)
    text = text.replace("’", "'").replace("‘", "'")
    return _WHITESPACE.sub(" ", text).strip()


def derive_age_bucket(age: int) -> str:
    """Map an age to its bucket. Boundaries: 29->18-29, 30->30-44, 59->45-59, 60->60+."""
    if age < 30:
        return "18-29"
    if age < 45:
        return "30-44"
    if age < 60:
        return "45-59"
    return "60+"
