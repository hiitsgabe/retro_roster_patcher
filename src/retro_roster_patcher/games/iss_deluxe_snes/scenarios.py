"""Scenario mode: twelve matches under way, each a record, a title and a description.

* Records $8B:FCF8 (ROM 0x5FCF8, `RECORDS`), 15 bytes each: +2 and +3 the time
  left (BCD seconds, then minutes), +4 and +5 the two teams (ids doubled, in
  `rom.TEAMS` order; the first takes the set piece the scenario opens with,
  `PIECES`), +6 and +7 the score. The rest (the set piece, the players' spots)
  belongs to the scenario, so a record plays whatever its two slots hold.
* Titles $87:EE3B (ROM 0x3EE3B, `TITLES`), 56 bytes each in the menu font
  (`rom.encode_name`'s), padded with spaces: the 34-character matchup ("NO.7
  ROMANIA 0-1 POLAND"), then the 22-character situation ("1:43 ROMANIA'S
  F.K."). The box is 28 characters wide: the matchup's last six wrap under the
  TIME REMAINING label, so the matchup keeps to `SHOWN`.
* Descriptions $87:F12D (ROM 0x3F12D, `TEXTS`), 156 bytes each, six lines of
  26, in a second font (`TEXT_FONT`).

`write` re-points the records as `iss_snes.scenarios.repoint` pairs them and
rewrites the texts of every scenario on two patched slots, at those widths.
"""

from __future__ import annotations

import textwrap
from collections.abc import Collection

from ...core.text import to_ascii
from ..iss_snes.scenarios import repoint
from . import rom as R

COUNT = 12
RECORDS = 0x5FCF8
RECORD = 15
TITLES = 0x3EE3B
TITLE = 56
MATCHUP = 34
SHOWN = 28
TEXTS = 0x3F12D
TEXT = 156
LINE = 26
#: The set piece each scenario opens with, which its record holds in bytes not decoded.
PIECES = (
    "C.K.", "F.K.", "C.K.", "F.K.", "THROW IN", "F.K.",
    "F.K.", "G.K.", "F.K.", "C.K.", "F.K.", "C.K.",
)  # fmt: skip
TEXT_FONT = {" ": 0x00, "-": 0x26, ".": 0x29, "'": 0x50, ",": 0x52}
TEXT_FONT.update({str(d): 0x01 + d for d in range(10)})
TEXT_FONT.update({chr(65 + k): 0x0B + k for k in range(26)})
TEXT_FONT.update({chr(97 + k): 0x2A + k for k in range(26)})


def pairs(image: bytes | bytearray) -> list[tuple[int, int]]:
    at = [RECORDS + RECORD * k for k in range(COUNT)]
    return [(image[a + 4] // 2, image[a + 5] // 2) for a in at]


def write(image: bytearray, names: dict[int, str], blank: Collection[int] = ()) -> None:
    """Point the scenarios at slots `names` holds ({slot: the name the patch gave it}, every
    slot it wrote a team into; `blank` the slots it emptied, which no scenario keeps) and write
    the title and description of each on two of them."""
    old = pairs(image)
    if any(max(pair) >= len(R.TEAMS) for pair in old):
        return
    for k, (a, b) in enumerate(repoint(old, names, blank)):
        at = RECORDS + RECORD * k
        image[at + 4], image[at + 5] = 2 * a, 2 * b
        if a in names and b in names:
            title, text = _texts(k, image[at : at + RECORD], _upper(names[a]), _upper(names[b]))
            image[TITLES + TITLE * k : TITLES + TITLE * (k + 1)] = title
            image[TEXTS + TEXT * k : TEXTS + TEXT * (k + 1)] = text


def _upper(name: str) -> str:
    return " ".join("".join(c for c in to_ascii(name).upper() if c in R._FONT).split())


def _bcd(value: int) -> int:
    return (value >> 4) * 10 + (value & 15)


def _texts(k: int, record: bytes | bytearray, a: str, b: str) -> tuple[bytes, bytes]:
    """Scenario `k`'s title and description for `a` against `b` (upper case)."""
    minutes, seconds, ga, gb = _bcd(record[3]), _bcd(record[2]), record[6], record[7]
    clock = f"{minutes}:{seconds:02}"
    head, short_a, short_b = f"NO.{k + 1}".ljust(5), a, b
    while len(f"{head} {short_a} {ga}-{gb} {short_b}") > SHOWN:
        if len(short_a) >= len(short_b):
            short_a = short_a[:-1].rstrip()
        else:
            short_b = short_b[:-1].rstrip()
    situation = f"{clock} {short_a}'S {PIECES[k]}"
    if len(situation) > TITLE - MATCHUP:
        situation = f"{clock} {PIECES[k]}"
    title = _menu(f"{head} {short_a} {ga}-{gb} {short_b}", MATCHUP) + _menu(
        situation, TITLE - MATCHUP
    )

    left = f"{minutes} min {seconds} sec" if minutes else f"{seconds} seconds"
    if ga < gb:
        story = f"{a} trail {b} {ga}-{gb} with {left} to play. Turn it around."
    elif ga > gb:
        story = f"{a} lead {b} {ga}-{gb} with {left} to play. Hold on for the win."
    else:
        story = f"{a} and {b} are level at {ga}-{gb} with {left} to play. Find a winner."
    lines = textwrap.wrap(story, LINE)
    text = "".join(line.ljust(LINE) for line in lines)[:TEXT].ljust(TEXT)
    return title, bytes(TEXT_FONT.get(c, 0) for c in text)


def _menu(text: str, width: int) -> bytes:
    return bytes(R._FONT.get(c, 0) for c in text)[:width].ljust(width, b"\0")
