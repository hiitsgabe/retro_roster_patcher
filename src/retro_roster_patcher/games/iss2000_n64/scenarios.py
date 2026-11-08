"""Scenario mode: sixteen matches already under way.

The records are raw in an overlay the game DMAs as it is (RAM 0x80281D40): 16
(`COUNT`) of 434 bytes (`RECORD`) from ROM 0x4A5110 (`TABLE`), the two team ids
at +0x14/+0x15 (`TEAMS`). The score, the clock and the players involved (record
indices into each team's 22, from +0x24) belong to the scenario, so they suit
any team.

Each scenario has a header ("3:41, Second Half  England to Free Kick") and a
description, in the player-name code (`rom.encode_name`, with `:` `,` `?`),
0xFF 0x10 a line break and 0xFF 0x11 the end. Two tables of 32 RAM pointers
reach them, the 16 headers then the 16 descriptions in record order: the
English at ROM 0x50C484, in an overlay at RAM 0x802DA580 = ROM 0x50B7A0, and the
Spanish at 0x50D8C0, the same RAM 0x1300 bytes further on (`TEXTS`). Strings
follow one another, so each may take the room up to the next one. The box draws
a description from its second line: after a header of two lines it starts with
an empty one.

`write` re-points the records and, for a scenario whose teams changed, rewrites
its texts in place, each within its own room and its end mark moved no further:
the header keeps its clock and set piece with the new taker's name, the
description is a generic blurb. No pointer moves.
"""

from __future__ import annotations

import hashlib
import re
import struct
import textwrap
from collections.abc import Mapping, Sequence

from . import rom as R

TABLE = 0x4A5110
RECORD = 434
COUNT = 16
TEAMS = 0x14
#: (pointer table, ROM offset minus RAM address) of the English and the Spanish texts.
TEXTS = (
    (0x50C484, 0x50B7A0 - 0x802DA580),
    (0x50D8C0, 0x50B7A0 + 0x1300 - 0x802DA580),
)
#: The cartridge's pairs: England-Scotland, Italy-Denmark, ... Argentina-Brazil.
RETAIL = (
    (14, 15), (2, 7), (0, 38), (1, 43), (3, 49), (25, 24), (4, 8), (72, 69),
    (16, 2), (28, 20), (19, 14), (26, 9), (30, 4), (96, 69), (43, 33), (70, 69),
)  # fmt: skip
#: The side whose set piece the header names (England's free kick, the Ukraine's penalty).
TAKER = (0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0)
#: The first 8 hex digits of the SHA-1 of each scenario's English header and description
#: (their whole room) on the cartridge: a text no patch has rewritten.
_RETAIL_TEXT = (
    "13ac555b4a91e04de767caf6cef5558b5fc2721fb5a8213cbdb8145cf8ca907c"
    "e5f0f1ba95e51d3497df8317e6fdf20d6341ab34ae8b6ac5a01e8e70e798a14b"
)
#: The widest header line seen whole in the box, and the description's lines.
HEADER_LINE = 41
LINE = 28
BREAK, END = b"\xff\x10", b"\xff\x11"
_CODES = {**R._CODES, ":": 0x42, ",": 0x4A, "?": 0x40}
_CHARS = {v: k for k, v in _CODES.items()}
_HEADER = (
    re.compile(r"(\d:\d\d, Second Half|Overtime)[| ]+(.*) to ([^|]+)\|?"),
    re.compile(r"(\d:\d\d, Segundo Tiempo|Tiempo Extra)[| ]+(.*?)[| ]+((?:a cobrar|con) [^|]+)\|?"),
)


def read(image: bytes | bytearray) -> list[tuple[int, int]]:
    """Each scenario's (first, second) team id."""
    return [
        (image[TABLE + k * RECORD + TEAMS], image[TABLE + k * RECORD + TEAMS + 1])
        for k in range(COUNT)
    ]


def strings(image: bytes | bytearray, lang: int) -> list[tuple[int, int]]:
    """(ROM offset, room) of language `lang`'s 32 strings: the headers, then the descriptions."""
    table, delta = TEXTS[lang]
    starts = [struct.unpack_from(">I", image, table + 4 * k)[0] + delta for k in range(2 * COUNT)]
    order = sorted(set(starts)) + [table]
    return [(s, order[order.index(s) + 1] - s) for s in starts]


def decode(raw: bytes | bytearray) -> str:
    """A string up to its end mark, "|" for each line break (unknown codes read "~")."""
    out, k = [], 0
    while k < len(raw) and raw[k : k + 2] != END:
        if raw[k : k + 2] == BREAK:
            out.append("|")
            k += 2
            continue
        out.append(_CHARS.get(raw[k], "~"))
        k += 1
    return "".join(out)


def encode(text: str) -> bytes:
    """`text` ("|" a line break) in the game's code, without the end mark; characters
    the code lacks are dropped ("&" reads "and")."""
    from ...core.text import to_ascii

    out = bytearray()
    for ch in to_ascii(text.replace("&", "and")):
        if ch == "|":
            out += BREAK
        elif ch in _CODES:
            out.append(_CODES[ch])
    return bytes(out)


def _fits(text: str, room: int, width: int) -> bool:
    return len(encode(text)) + len(END) <= room and all(
        len(line) <= width for line in text.split("|")
    )


def header(old: str, name: str, code: str, lang: int, room: int) -> str | None:
    """Header `old` with `name` (else `code`) taking its set piece, within `room` bytes;
    None when `old` is not a header this module reads."""
    m = _HEADER[lang].fullmatch(old)
    if m is None:
        return None
    clock, piece = m.group(1), m.group(3)
    for who in (name, code[:3]):
        layouts = (
            (f"{clock}  {who} to {piece}|", f"{clock}|  {who} to {piece}|")
            if lang == 0
            else (f"{clock}|  {who} {piece}|",)
        )
        for text in layouts:
            if _fits(text, room, HEADER_LINE):
                return text
    return None


def blurb(lang: int, a: str, b: str, room: int, lead: str = "") -> str:
    """A description for `a` against `b` in `room` bytes, after `lead`, in lines of
    `LINE` characters."""
    texts = (
        (f"{a} take on {b} with everything still to play for. Can you lead your team to the win?",
         f"{a} take on {b}. Can you win it?", f"{a} v {b}")
        if lang == 0
        else (f"{a} y {b} se lo juegan todo. Lleva a tu equipo a la victoria.", f"{a} contra {b}")
    )  # fmt: skip
    text = ""
    for text in texts:
        text = lead + "|".join(textwrap.wrap(text, LINE)) + "|"
        if _fits(text, room, LINE):
            break
    return text


def _put(image: bytearray, at: int, room: int, text: str) -> None:
    data = encode(text) + END
    image[at : at + room] = data[:room].ljust(room, b"\0")


def _text_hash(image: bytes | bytearray, k: int) -> str:
    spots = strings(image, 0)
    h = hashlib.sha1()
    for at, room in (spots[k], spots[COUNT + k]):
        h.update(image[at : at + room])
    return h.hexdigest()[:8]


def write(
    image: bytearray,
    pairs: Sequence[tuple[int, int]],
    names: Mapping[int, tuple[str, str]],
) -> None:
    """Point scenario k at `pairs[k]`; a scenario not on the cartridge's teams (or whose
    texts an earlier patch rewrote) gets texts naming its teams, `names` {slot: (name,
    short code)}."""
    redo = [
        k
        for k in range(COUNT)
        if (tuple(pairs[k]) != RETAIL[k] or _text_hash(image, k) != _RETAIL_TEXT[8 * k : 8 * k + 8])
        and all(s in names for s in pairs[k])
    ]
    for k, (a, b) in enumerate(pairs):
        image[TABLE + k * RECORD + TEAMS : TABLE + k * RECORD + TEAMS + 2] = bytes((a, b))
    for lang in range(len(TEXTS)):
        spots = strings(image, lang)
        for k in redo:
            (h_at, h_room), (d_at, d_room) = spots[k], spots[COUNT + k]
            taker = names[pairs[k][TAKER[k]]]
            head = header(decode(image[h_at : h_at + h_room]), *taker, lang, h_room)
            if head is None:
                continue
            _put(image, h_at, h_room, head)
            lead = " |" if head.count("|") > 1 else ""
            first, second = (names[s][0] for s in pairs[k])
            _put(image, d_at, d_room, blurb(lang, first, second, d_room, lead))
