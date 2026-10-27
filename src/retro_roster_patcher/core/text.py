"""Text helpers shared by the game writers."""

from __future__ import annotations

import unicodedata

#: Letters Unicode does not decompose into a base letter and a mark.
_TRANSLIT = str.maketrans(
    {
        "Ø": "O",
        "ø": "o",
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "ß": "ss",
        "Ł": "L",
        "ł": "l",
        "Đ": "D",
        "đ": "d",
        "Þ": "Th",
        "þ": "th",
        "ı": "i",
    }
)


def to_ascii(text: str) -> str:
    """`text` with its diacritics stripped, the letters Unicode does not
    decompose spelled out (`_TRANSLIT`) and anything else outside ASCII
    dropped: "Tim Stützle" becomes "Tim Stutzle", "Ødegaard" "Odegaard"."""
    nfkd = unicodedata.normalize("NFKD", text.translate(_TRANSLIT))
    return "".join(c for c in nfkd if not unicodedata.combining(c) and ord(c) < 128)
