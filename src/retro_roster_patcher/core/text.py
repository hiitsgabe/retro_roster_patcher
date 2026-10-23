"""Text helpers shared by the game writers."""

from __future__ import annotations

import unicodedata


def to_ascii(text: str) -> str:
    """`text` with its diacritics stripped and anything else outside ASCII
    dropped: "Tim Stützle" becomes "Tim Stutzle", not "Tim St?tzle"."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c) and ord(c) < 128)
