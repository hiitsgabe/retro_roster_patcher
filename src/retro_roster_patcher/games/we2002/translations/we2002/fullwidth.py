"""Translate the community English patch's full-width text into es/fr/pt.

`w202-english.ppf` writes most of its English (help lines, match and training
menus, memory card dialogs, Edit mode) as Shift-JIS full-width letters, often as
byte-level diffs against the Japanese text. `menu_records` translates plain
ASCII records one record at a time, so full-width text, and ASCII strings the
patch writes in pieces, stayed English under a Spanish, French or Portuguese
patch.

After the language PPF is applied, every string the community patch touches is
read back from the image whole, matched against `FULLWIDTH` (or `ASCII`) by its
English text and rewritten in place: letters full-width, spaces and line
breaks as the English has them, padded with spaces to the same byte length.
The game's font has no accented letters, so accents are dropped.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, BinaryIO

from .fullwidth_text import ASCII, FULLWIDTH
from .menu_records import _parse_ppf2

_SECTOR = 2352
_DATA = 24  # MODE2/2352: 24-byte header, then 2048 bytes of user data
_USER = 2048

#: The patcher's own kanji team-name table (`_OFS_NOMI_SQK` onward): it writes
#: those names itself, so they are never translated here.
_NAMES = (0x1E8D8C, 0x1E9420)


def _bounds(offset: int) -> tuple[int, int]:
    """The user-data span of the sector holding `offset`."""
    start = offset // _SECTOR * _SECTOR + _DATA
    return start, start + _USER


def _string_at(image: bytes | bytearray, offset: int) -> tuple[int, int] | None:
    """The NUL-delimited string around `offset`, inside its sector's user data."""
    lo, hi = _bounds(offset)
    if not lo <= offset < hi or image[offset] == 0:
        return None
    a = offset
    while a > lo and image[a - 1] != 0:
        a -= 1
    b = offset
    while b < hi and image[b] != 0:
        b += 1
    if a == lo or b == hi:
        return None  # may continue in the next or previous sector: leave it
    return a, b


def _to_ascii(raw: bytes) -> str | None:
    """Full-width English as plain text, or None if it is not that."""
    try:
        text = raw.decode("cp932")
    except UnicodeDecodeError:
        return None
    if not any(0xFF21 <= ord(c) <= 0xFF5A for c in text):
        return None
    return "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in text)


def _encode(text: str) -> bytes | None:
    """`text` as the English patch writes it: letters full-width, spaces and
    control characters single bytes. None if a character has no glyph."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    out = bytearray()
    for c in plain:
        if c in " \n\x0b":
            out += c.encode("ascii")
        elif "!" <= c <= "~":
            out += chr(ord(c) + 0xFEE0).encode("cp932")
        else:
            try:
                out += c.encode("cp932")
            except UnicodeEncodeError:
                return None
    return bytes(out)


def _key(english: str) -> str:
    """The English as `FULLWIDTH` holds it: no trailing spaces, and no single
    character the patch left after a run of them (a Japanese leftover)."""
    text = english.rstrip(" ")
    match = re.fullmatch(r"(.*\S) {2,}\S", text, re.S)
    return match.group(1) if match else text


def _char_ends(raw: bytes) -> list[int]:
    """The byte offset after each character of `raw` (Shift-JIS: a lead byte
    in 0x81-0x9F or 0xE0-0xFC starts a two-byte character)."""
    ends, i = [], 0
    while i < len(raw):
        i += 2 if 0x81 <= raw[i] <= 0x9F or 0xE0 <= raw[i] <= 0xFC else 1
        ends.append(i)
    return ends


def _rewrite(raw: bytes, english: str, translated: str) -> bytes | None:
    """`raw` with its English replaced by `translated` in the same bytes.

    The budget is the English text plus the spaces after it; anything after
    those (a leftover Japanese character) is kept as it is. `translated` may
    list shorter fallbacks after a `|`, for a string the disc holds in
    smaller fields too; the first that fits is used.
    """
    key = _key(english)
    if not key:
        return None
    end = _char_ends(raw)[len(key) - 1]
    while end < len(raw) and raw[end] == 0x20:
        end += 1
    for choice in translated.split("|"):
        new = _encode(choice)
        if new is not None and len(new) <= end:
            return new + b" " * (end - len(new)) + raw[end:]
    return None


def _rewrite_ascii(raw: bytes, translated: str) -> bytes | None:
    """`translated` in `raw`'s bytes, padded with spaces; None if it is longer."""
    plain = "".join(
        c for c in unicodedata.normalize("NFKD", translated) if not unicodedata.combining(c)
    )
    try:
        new = plain.encode("ascii")
    except UnicodeEncodeError:
        return None
    return new.ljust(len(raw)) if len(new) <= len(raw) else None


def translate_image(image: bytearray, community_ppf: str, lang: str) -> int:
    """Rewrite in `image` every English string `community_ppf` touches that
    `FULLWIDTH` or `ASCII` translates into `lang`. Returns how many."""
    done = 0
    seen: set[int] = set()
    for offset, _data in _parse_ppf2(community_ppf):
        if _NAMES[0] <= offset < _NAMES[1]:
            continue
        span = _string_at(image, offset)
        if span is None or span[0] in seen:
            continue
        seen.add(span[0])
        raw = bytes(image[span[0] : span[1]])
        english = _to_ascii(raw)
        if english is not None:
            translated = FULLWIDTH.get(_key(english), {}).get(lang)
            new = _rewrite(raw, english, translated) if translated else None
        elif all(0x20 <= b < 0x7F for b in raw):
            translated = ASCII.get(raw.decode("ascii").rstrip(" "), {}).get(lang)
            new = _rewrite_ascii(raw, translated) if translated else None
        else:
            continue
        if new is not None:
            image[span[0] : span[1]] = new
            done += 1
    return done


class _Sectors:
    """A disc image file, indexed like bytes, read and written a sector at a time."""

    def __init__(self, f: BinaryIO) -> None:
        self._f = f
        self._cache: dict[int, bytearray] = {}
        self._dirty: set[int] = set()

    def _sector(self, n: int) -> bytearray:
        if n not in self._cache:
            self._f.seek(n * _SECTOR)
            self._cache[n] = bytearray(self._f.read(_SECTOR).ljust(_SECTOR, b"\0"))
        return self._cache[n]

    def __getitem__(self, key: int | slice) -> Any:
        if isinstance(key, slice):
            n = key.start // _SECTOR
            return self._sector(n)[key.start - n * _SECTOR : key.stop - n * _SECTOR]
        return self._sector(key // _SECTOR)[key % _SECTOR]

    def __setitem__(self, key: slice, value: bytes) -> None:
        n = key.start // _SECTOR
        self._sector(n)[key.start - n * _SECTOR : key.stop - n * _SECTOR] = value
        self._dirty.add(n)

    def flush(self) -> None:
        for n in sorted(self._dirty):
            self._f.seek(n * _SECTOR)
            self._f.write(self._cache[n])


def translate_file(image_path: str, community_ppf: str, lang: str) -> int:
    """`translate_image` on a disc image file, touching only the sectors it needs."""
    with open(image_path, "r+b") as f:
        sectors = _Sectors(f)
        done = translate_image(sectors, community_ppf, lang)  # type: ignore[arg-type]
        sectors.flush()
    return done


def english_strings(image: bytes | bytearray, community_ppf: str) -> dict[int, str]:
    """Every full-width English string the community patch leaves in `image`,
    by offset. For building and checking `FULLWIDTH`."""
    found: dict[int, str] = {}
    for offset, _data in _parse_ppf2(community_ppf):
        if _NAMES[0] <= offset < _NAMES[1]:
            continue
        span = _string_at(image, offset)
        if span is None or span[0] in found:
            continue
        english = _to_ascii(bytes(image[span[0] : span[1]]))
        if english is not None:
            found[span[0]] = _key(english)
    return found
