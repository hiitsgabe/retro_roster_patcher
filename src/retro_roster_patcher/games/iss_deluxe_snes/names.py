"""Team names on the scoreboard and the cup screens: 32x8 BG3 pictures.

$81:E6C1 (ROM 0xE6C1) holds a 16-bit pointer per team (42) to a Konami
stream of four 2bpp tiles, the name in white (1) and grey (2) on a black (3)
box over a transparent background, centred. Bit 15 of a stream's header marks
tiles stored plane by plane (`flags` has the same), which the game
interleaves. The bank comes from the template at $81:E6B4 (`TEMPLATE`, bank
$9A on the cartridge) and the cup screens load all 42 from the list at
$82:8F21 (`RAM_LIST`). `write` redraws the names it is given in the
cartridge's own letters (`FONT`, read off its names; Q, V, the digits and the
hyphen drawn to match) and moves every name to `ROOM`, in bank $AF's free
space after the flags.
"""

from __future__ import annotations

import struct

from ...core.text import to_ascii
from ..iss_snes.konami import compress, decompress
from .flags import BANK_AF, FREE

POINTERS = 0xE6C1
TEMPLATE = 0xE6B4  # a word, then two entries of a word and a 24-bit source
RAM_LIST = 0x10F21  # a word, then 42 entries of a 24-bit target and source
TEAMS = 42
WIDTH = 32
ROOM = (FREE[1] - 0x1800, FREE[1] - 0x400)

_ROWS = {
    "A": ("+#+", "# #", "# #", "###", "# #", "# #", "# #"),
    "B": ("##+", "# #", "# #", "## ", "# #", "# #", "##+"),
    "C": ("+#+", "# #", "#  ", "#  ", "# #", "# #", "+#+"),
    "D": ("## ", "# #", "# #", "# #", "# #", "# #", "## "),
    "E": ("###", "#  ", "#  ", "## ", "#  ", "#  ", "###"),
    "F": ("###", "#  ", "## ", "#  ", "#  ", "#  ", "#  "),
    "G": ("+#+", "# #", "#  ", "#+#", "# #", "# #", "+#+"),
    "H": ("# #", "# #", "###", "# #", "# #", "# #", "# #"),
    "I": ("#", "#", "#", "#", "#", "#", "#"),
    "J": (" ###", "  # ", "  # ", "  # ", "  # ", "# # ", "+#+ "),
    "K": ("# #", "# #", "## ", "# #", "# #", "# #", "# #"),
    "L": ("#  ", "#  ", "#  ", "#  ", "#  ", "#  ", "###"),
    "M": ("# #", "###", "#+#", "# #", "# #", "# #", "# #"),
    "N": ("# #", "#+#", "###", "###", "###", "#+#", "# #"),
    "O": ("+#+", "# #", "# #", "# #", "# #", "# #", "+#+"),
    "P": ("##+", "# #", "# #", "##+", "#  ", "#  ", "#  "),
    "Q": ("+#+", "# #", "# #", "# #", "# #", "#+#", "+##"),
    "R": ("##+", "# #", "# #", "## ", "# #", "# #", "# #"),
    "S": ("+#+", "# #", "+# ", " ++", "# #", "# #", "+#+"),
    "T": ("###", " # ", " # ", " # ", " # ", " # ", " # "),
    "U": ("# #", "# #", "# #", "# #", "# #", "# #", "+#+"),
    "V": ("# #", "# #", "# #", "# #", "# #", "+#+", " # "),
    "W": ("#   #", "# # #", "# # #", "# # #", "# # #", "+++++", " # # "),
    "X": ("# #", "# #", " # ", " # ", " # ", "# #", "# #"),
    "Y": ("# #", "# #", "# #", " # ", " # ", " # ", " # "),
    "Z": ("###", "  #", " ++", " # ", "++ ", "#  ", "###"),
    "0": ("+#+", "# #", "# #", "# #", "# #", "# #", "+#+"),
    "1": (" #", "##", " #", " #", " #", " #", " #"),
    "2": ("+#+", "# #", "  #", " ++", "++ ", "#  ", "###"),
    "3": ("##+", "  #", "  #", " #+", "  #", "  #", "##+"),
    "4": ("# #", "# #", "# #", "###", "  #", "  #", "  #"),
    "5": ("###", "#  ", "## ", "  #", "  #", "# #", "+#+"),
    "6": ("+#+", "#  ", "## ", "# #", "# #", "# #", "+#+"),
    "7": ("###", "  #", "  #", " # ", " # ", " # ", " # "),
    "8": ("+#+", "# #", "# #", "+#+", "# #", "# #", "+#+"),
    "9": ("+#+", "# #", "# #", "+##", "  #", "  #", "+#+"),
    ".": (" ", " ", " ", " ", " ", " ", "#"),
    "-": ("   ", "   ", "   ", "###", "   ", "   ", "   "),
}
_VALUE = {"#": 1, "+": 2, " ": 3}
#: Each character's columns, each 7 values top to bottom.
FONT = {
    ch: [[_VALUE[r[x]] for r in rows] for x in range(len(rows[0]))] for ch, rows in _ROWS.items()
}
FONT[" "] = []  # a word gap: the two letter gaps around it


def _text(name: str) -> str:
    return "".join(c for c in to_ascii(name).upper() if c in FONT).strip()


def width(text: str) -> int:
    return sum(len(FONT[c]) for c in text) + max(len(text) - 1, 0)


def fit(*names: str) -> str:
    """The first of `names` that fits 32 pixels, else the part of the first
    before a hyphen (ATLETICO-MG reads ATLETICO), else the last cut short."""
    texts = [_text(n) for n in names if _text(n)]
    if not texts:
        return ""
    for t in [*texts, texts[0].split("-")[0]]:
        if width(t) <= WIDTH:
            return t
    t = texts[-1]
    while width(t) > WIDTH:
        t = t[:-1].rstrip()
    return t


def render(text: str) -> list[list[int]]:
    """8 rows of 32 values: `text` centred in its black box, row 7 the box's
    bottom edge."""
    cols: list[list[int]] = []
    for i, ch in enumerate(text):
        if i:
            cols.append([3] * 7)
        cols += FONT[ch]
    left = (WIDTH - len(cols)) // 2
    rows = [[0] * WIDTH for _ in range(8)]
    for x, col in enumerate(cols):
        for y, v in enumerate(col):
            rows[y][left + x] = v
        rows[7][left + x] = 3
    return rows


def _tiles(rows: list[list[int]]) -> bytes:
    out = bytearray(64)
    for x in range(WIDTH):
        for y in range(8):
            v = rows[y][x]
            out[16 * (x // 8) + 2 * y] |= (v & 1) << (7 - x % 8)
            out[16 * (x // 8) + 2 * y + 1] |= (v >> 1) << (7 - x % 8)
    return bytes(out)


def blob(rows: list[list[int]]) -> bytes:
    """The smaller of the two ways to store a name."""
    tiles = _tiles(rows)
    planar = bytearray(64)
    for t in range(4):
        for r in range(8):
            planar[16 * t + r] = tiles[16 * t + 2 * r]
            planar[16 * t + 8 + r] = tiles[16 * t + 2 * r + 1]
    flat, split = compress(tiles), bytearray(compress(bytes(planar)))
    split[1] |= 0x80
    return min(flat, bytes(split), key=len)


def _bank(image: bytes | bytearray) -> int:
    return (image[TEMPLATE + 6] & 0x7F) * 0x8000


def current(image: bytes | bytearray, team: int) -> bytes:
    at = _bank(image) + (struct.unpack_from("<H", image, POINTERS + 2 * team)[0] & 0x7FFF)
    return bytes(image[at : at + (struct.unpack_from("<H", image, at)[0] & 0x7FFF)])


def read(image: bytes | bytearray, team: int) -> list[list[int]]:
    """`team`'s name as 8 rows of 32 values."""
    b = current(image, team)
    raw = decompress(b)
    if b[1] & 0x80:
        tiles = bytearray(raw)
        for t in range(4):
            for r in range(8):
                tiles[16 * t + 2 * r] = raw[16 * t + r]
                tiles[16 * t + 2 * r + 1] = raw[16 * t + 8 + r]
        raw = bytes(tiles)

    def bit(x: int, y: int, plane: int) -> int:
        return raw[16 * (x // 8) + 2 * y + plane] >> (7 - x % 8) & 1

    return [[bit(x, y, 0) | bit(x, y, 1) << 1 for x in range(WIDTH)] for y in range(8)]


def write(image: bytearray, names: dict[int, str]) -> None:
    """Draw each team in `names` ({team: text as `fit` gives it}) and move
    every name to `ROOM`."""
    blobs = [blob(render(names[t])) if t in names else current(image, t) for t in range(TEAMS)]
    start, end = ROOM
    if sum(map(len, blobs)) > end - start:
        raise ValueError(f"The team names need {sum(map(len, blobs))} bytes; {end - start} free")
    image[start:end] = b"\xff" * (end - start)
    at = start
    for team, b in enumerate(blobs):
        image[at : at + len(b)] = b
        struct.pack_into("<H", image, POINTERS + 2 * team, 0x8000 | at & 0x7FFF)
        source = (BANK_AF << 16 | 0x8000 | at & 0x7FFF).to_bytes(3, "little")
        image[RAM_LIST + 2 + 6 * team + 3 : RAM_LIST + 2 + 6 * team + 6] = source
        at += len(b)
    for k in range(2):
        image[TEMPLATE + 2 + 5 * k + 4] = BANK_AF
