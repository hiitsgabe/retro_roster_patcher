"""Team select's pages: which teams each shows and the label over them.

* $81:DA3F (`ORDER`) lists the teams page by page, six a page, as team id
  times two: six pages of nations, then a seventh of the special teams.
  `write` puts the mapped clubs first, in mapping order, and the nations
  after them in the order the cartridge had, so the clubs fill the first
  pages together however the slots were picked.
* $87:C54F points each page at its label: 16 bytes in bank $87, eight
  columns of top tiles and eight of bottom ones (0 blank, else BG3 tile
  byte + 0x10E). The tiles are a Konami stream at $9E:EC78 that the template
  at $82:8EE0 loads to VRAM $4E00, 62 tiles (bytes 0xB2-0xEF): EUROPE and
  its digits, ASIA-AFRICA, N.S.AMERICA, ALLSTAR.
* A page with a club is labelled with its league (`label`), in the label
  font (`GLYPHS`, the cartridge's letters and the rest drawn to match); the
  others keep their own. `write` packs the tiles anew, the same ones once,
  and stores the stream in bank $AF's free space.
"""

from __future__ import annotations

import struct

from ...core.text import to_ascii
from ..iss_snes.konami import compress_large, decompress
from .flags import BANK_AF, FREE

ORDER = 0xDA3F
NATIONS = 36
PER_PAGE = 6
PAGES = 7
LABELS = 0x3C54F  # $87:C54F: seven pointers into bank $87
BANK_87 = 0x38000
TEMPLATE_SOURCE = 0x10EEE  # $82:8EE0's entry for VRAM $4E00: its 24-bit source
FIRST_BYTE, LAST_BYTE = 0xB2, 0xEF  # the 62 tiles VRAM holds from $4E00
ROOM = (FREE[1] - 0x2000, FREE[1] - 0x1800)
WIDTH = 64

_G = {
    "A": (" ### ", "#+ +#", "#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #",
          "#   #", "#   #", "#   #"),
    "B": ("#### ", "#  +#", "#   #", "#   #", "#  +#", "#### ", "#  +#", "#   #", "#   #",
          "#   #", "#  +#", "#### "),
    "C": (" ### ", "#+ +#", "#   #", "#    ", "#    ", "#    ", "#    ", "#    ", "#    ",
          "#   #", "#+ +#", " ### "),
    "D": ("#### ", "#  +#", "#   #", "#   #", "#   #", "#   #", "#   #", "#   #", "#   #",
          "#   #", "#  +#", "#### "),
    "E": ("#####", "#    ", "#    ", "#    ", "#    ", "#### ", "#    ", "#    ", "#    ",
          "#    ", "#    ", "#####"),
    "F": ("#####", "#    ", "#    ", "#    ", "#    ", "#### ", "#    ", "#    ", "#    ",
          "#    ", "#    ", "#    "),
    "G": (" ### ", "#+ +#", "#   #", "#    ", "#    ", "#  ##", "#   #", "#   #", "#   #",
          "#   #", "#+ +#", " ### "),
    "H": ("#   #", "#   #", "#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #",
          "#   #", "#   #", "#   #"),
    "I": ("#",) * 12,
    "J": ("   #", "   #", "   #", "   #", "   #", "   #", "   #", "   #", "#  #", "#  #",
          "#+ #", " ## "),
    "K": ("#   #", "#  # ", "#  # ", "# #  ", "# #  ", "##   ", "# #  ", "# #  ", "#  # ",
          "#  # ", "#   #", "#   #"),
    "L": ("#   ",) * 11 + ("####",),
    "M": ("#   #", "## ##", "## ##", "#+#+#", "# # #", "# # #", "#   #", "#   #", "#   #",
          "#   #", "#   #", "#   #"),
    "N": ("#   #", "##  #", "##  #", "# # #", "# # #", "# # #", "# # #", "#  ##", "#  ##",
          "#   #", "#   #", "#   #"),
    "O": (" ### ", "#+ +#", "#   #", "#   #", "#   #", "#   #", "#   #", "#   #", "#   #",
          "#   #", "#+ +#", " ### "),
    "P": ("#### ", "#  +#", "#   #", "#   #", "#  +#", "#### ", "#    ", "#    ", "#    ",
          "#    ", "#    ", "#    "),
    "Q": (" ### ", "#+ +#", "#   #", "#   #", "#   #", "#   #", "#   #", "#   #", "# # #",
          "#  ##", "#+ +#", " ## #"),
    "R": ("#### ", "#  +#", "#   #", "#   #", "#  +#", "#### ", "#  # ", "#  ++", "#   #",
          "#   #", "#   #", "#   #"),
    "S": (" ### ", "#+ +#", "#   #", "++   ", " ##  ", "  +# ", "   ++", "    #", "#   #",
          "#   #", "#+ +#", " ### "),
    "T": ("#####",) + ("  #  ",) * 11,
    "U": ("#   #",) * 10 + ("#+ +#", " ### "),
    "V": ("#   #",) * 7 + (" # # ", " # # ", " # # ", "  #  ", "  #  "),
    "W": ("#   #",) * 6 + ("# # #", "# # #", "# # #", "#+#+#", "## ##", "#   #"),
    "X": ("#   #", "#   #", " # # ", " # # ", "  #  ", "  #  ", "  #  ", "  #  ", " # # ",
          " # # ", "#   #", "#   #"),
    "Y": ("#   #", "#   #", "#   #", " # # ", " # # ", "  #  ", "  #  ", "  #  ", "  #  ",
          "  #  ", "  #  ", "  #  "),
    "Z": ("#####", "    #", "   # ", "   # ", "  #  ", "  #  ", "  #  ", " #   ", " #   ",
          "#    ", "#    ", "#####"),
    "0": (" ### ", "#+ +#", "#   #", "#  ##", "#  ##", "# # #", "# # #", "##  #", "##  #",
          "#   #", "#+ +#", " ### "),
    "1": (" # ", "## ") + (" # ",) * 9 + ("###",),
    "2": (" ### ", "#+ +#", "#   #", "    #", "   ++", "  +# ", " +#  ", " #   ", "#    ",
          "#    ", "#    ", "#####"),
    "3": (" ### ", "#+ +#", "    #", "    #", "    #", "  ## ", "    #", "    #", "    #",
          "    #", "#+ +#", " ### "),
    "4": ("   # ", "  ## ", "  ## ", " # # ", " # # ", "#  # ", "#  # ", "#####", "   # ",
          "   # ", "   # ", "   # "),
    "5": ("#####", "#    ", "#    ", "#    ", "#### ", "    #", "    #", "    #", "    #",
          "#   #", "#+ +#", " ### "),
    "6": (" ### ", "#+ +#", "#    ", "#    ", "#### ", "#  +#", "#   #", "#   #", "#   #",
          "#   #", "#+ +#", " ### "),
    "7": ("#####", "    #", "    #", "   # ", "   # ", "  #  ", "  #  ", "  #  ", "  #  ",
          "  #  ", "  #  ", "  #  "),
    "8": (" ### ", "#+ +#", "#   #", "#   #", "#+ +#", " ### ", "#+ +#", "#   #", "#   #",
          "#   #", "#+ +#", " ### "),
    "9": (" ### ", "#+ +#", "#   #", "#   #", "#   #", "#+  #", " ####", "    #", "    #",
          "    #", "#+ +#", " ### "),
    ".": (" ",) * 11 + ("#",),
    "-": ("   ",) * 5 + ("###",) + ("   ",) * 6,
    " ": ("  ",) * 12,
}  # fmt: skip
GLYPHS = {ch: [[{"#": 1, "+": 2}.get(r[x], 0) for r in rows] for x in range(len(rows[0]))]
          for ch, rows in _G.items()}  # fmt: skip


def _text(name: str) -> str:
    return " ".join(
        "".join(c for c in w if c in GLYPHS) for w in to_ascii(name).upper().split()
    ).strip()


def _width(text: str) -> int:
    return sum(len(GLYPHS[c]) for c in text) + len(text) + 1


#: A league's leading nationality, dropped: ENGLISH PREMIER LEAGUE reads PREMIER.
_NATIONALITY = {
    "ENGLISH", "SPANISH", "GERMAN", "ITALIAN", "FRENCH", "PORTUGUESE", "DUTCH", "SCOTTISH",
    "TURKISH", "BELGIAN", "MEXICAN", "ARGENTINE", "BRAZILIAN", "AMERICAN", "JAPANESE", "SAUDI",
}  # fmt: skip


def label(league: str) -> str:
    """`league` as far as whole words fit the label (BRASILEIRAO SERIE A reads
    BRASILEIRAO), past a leading nationality, the first word cut short when
    even that does not."""
    words = _text(league).split()
    if len(words) > 1 and words[0] in _NATIONALITY:
        words = words[1:]
    if not words:
        return ""
    out = words[0]
    for w in words[1:]:
        if _width(f"{out} {w}") > WIDTH:
            break
        out = f"{out} {w}"
    while _width(out) > WIDTH:
        out = out[:-1]
    return out


def render(text: str) -> list[list[int]]:
    """16 rows of 64 values (0 clear, 1 white, 2 grey, 3 black outline):
    `text` centred, the letters in rows 1-12."""
    cols: list[list[int]] = []
    for i, ch in enumerate(text):
        if i:
            cols.append([0] * 12)
        cols += GLYPHS[ch]
    left = (WIDTH - len(cols)) // 2
    g = [[0] * WIDTH for _ in range(16)]
    for x, col in enumerate(cols):
        for y, v in enumerate(col):
            g[1 + y][left + x] = v
    for y in range(16):
        for x in range(WIDTH):
            if g[y][x] == 0 and any(
                0 <= y + dy < 16 and 0 <= x + dx < WIDTH and g[y + dy][x + dx] in (1, 2)
                for dy in (-1, 0, 1)
                for dx in (-1, 0, 1)
            ):
                g[y][x] = 3
    return g


def _tile(g: list[list[int]], col: int, row: int) -> bytes:
    out = bytearray(16)
    for y in range(8):
        for x in range(8):
            v = g[8 * row + y][8 * col + x]
            out[2 * y] |= (v & 1) << (7 - x)
            out[2 * y + 1] |= (v >> 1) << (7 - x)
    return bytes(out)


def _source(image: bytes | bytearray) -> int:
    address = int.from_bytes(image[TEMPLATE_SOURCE : TEMPLATE_SOURCE + 3], "little")
    return (address >> 16 & 0x7F) * 0x8000 + (address & 0x7FFF)


def _string_at(image: bytes | bytearray, page: int) -> int:
    return BANK_87 + (struct.unpack_from("<H", image, LABELS + 2 * page)[0] & 0x7FFF)


def _tiles_of(image: bytes | bytearray, page: int) -> list[bytes]:
    """The 16 tiles of `page`'s label, top row then bottom (blank as zeros)."""
    raw = decompress(bytes(image), _source(image))
    at = _string_at(image, page)
    return [
        raw[16 * (b - FIRST_BYTE) : 16 * (b - FIRST_BYTE) + 16] if b else bytes(16)
        for b in image[at : at + 16]
    ]


def order(image: bytes | bytearray) -> list[int]:
    """The 36 nations' places on the pages, as team ids."""
    return [b // 2 for b in image[ORDER : ORDER + NATIONS]]


def write(image: bytearray, clubs: list[int], league: str) -> None:
    """Put `clubs` (team ids, in mapping order) on the first pages and label
    those pages `label(league)`; with no league the labels stay."""
    rest = [t for t in order(image) if t not in clubs]
    image[ORDER : ORDER + NATIONS] = bytes(2 * t for t in [*clubs, *rest][:NATIONS])
    text = label(league)
    tiles = [_tiles_of(image, p) for p in range(PAGES)]
    if text:
        g = render(text)
        new = [_tile(g, c, 0) for c in range(8)] + [_tile(g, c, 1) for c in range(8)]
        for p in range(NATIONS // PER_PAGE):
            if any(t in clubs for t in [*clubs, *rest][p * PER_PAGE : (p + 1) * PER_PAGE]):
                tiles[p] = new
    unique: dict[bytes, int] = {}
    strings = []
    for p in range(PAGES):
        s = bytearray()
        for t in tiles[p]:
            if not any(t):
                s.append(0)
                continue
            if t not in unique:
                unique[t] = FIRST_BYTE + len(unique)
            s.append(unique[t])
        strings.append(s)
    if FIRST_BYTE + len(unique) - 1 > LAST_BYTE:
        raise ValueError(f"The page labels need {len(unique)} tiles; VRAM holds 62")
    stream = compress_large(b"".join(unique))
    start, end = ROOM
    if len(stream) > end - start:
        raise ValueError(f"The page labels need {len(stream)} bytes; {end - start} free")
    image[start:end] = b"\xff" * (end - start)
    image[start : start + len(stream)] = stream
    pointer = BANK_AF << 16 | 0x8000 | start & 0x7FFF
    image[TEMPLATE_SOURCE : TEMPLATE_SOURCE + 3] = pointer.to_bytes(3, "little")
    for p, s in enumerate(strings):
        at = _string_at(image, p)
        image[at : at + 16] = s
