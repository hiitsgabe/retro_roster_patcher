"""The big team names: team select's header, today's game and the kick-off.

$98:A526 is a Konami stream of compressed OAM data, which $80:947E unpacks
into four RAM arrays from 7E:4F02: a byte of entries (42, one per team),
a word of sprites (411), a byte of sprites per entry, then the sprites' X
offsets, Y offsets, tiles and attributes, one block each. $82:F59A
(`POINTERS`) gives each team the RAM address of its entry (4F02 + 2 per
sprite before it). The arrays end where the next screen data starts
(7E:5238), so the 411 sprites are all there is: a club gets the sprites of
the nations it replaces, shared out by `write`.

The letters are OBJ tiles 0x80-0xEF, 8 pixels apart: a letter is two 8x8
sprites (the tile at y 4, the one 16 below at y 12); two letters side by
side in the font are one 16x16 sprite at y 8 (`_TILES`, read off the
cartridge's names). The stream is read through three load scripts
(`SCRIPTS`); `write` stores the new one in bank $AF's free space.
"""

from __future__ import annotations

import struct

from ...core.text import to_ascii
from ..iss_snes.konami import compress_large, decompress
from .flags import BANK_AF, FREE

POINTERS = 0x1759A
SCRIPTS = (0x10C21, 0x10E49, 0x116B5)  # 24-bit pointers to the stream
FIRST = 0x4F02
SPRITES = 411
ROOM = (FREE[1] - 0x400, FREE[1])
TEAMS = 42
MAX_LETTERS = 9
CENTRE = 40

_ROW8 = "ABCDEFGHIJKLMNOP"
_TILES: dict[int, str] = {0x80 + i: c for i, c in enumerate(_ROW8)}
_TILES |= {0xA0 + i: c for i, c in enumerate("QRSTUVWXYZ")}
_TILES |= {0xAB: "L", 0xAC: "A", 0xAD: "N", 0xAE: "D"}
_TILES |= {0xC0 + i: c for i, c in enumerate("CCOOGAMABEGECAS.")}
_TILES |= {0xE0: "A", 0xE1: "R", 0xE3: "I", 0xE4: "A", 0xE5: "Y", 0xE6: "R", 0xE7: "O"}
_TILES |= {0xE8: "A", 0xE9: "F"}
_SINGLE = {c: t for t, c in sorted(_TILES.items(), reverse=True)}
_PAIRS = {_TILES[t] + _TILES[t + 1]: t for t in sorted(_TILES) if t + 1 in _TILES and t & 15 != 15}

Sprite = tuple[int, int, int, int]  # x, y, tile, attribute


def text(name: str) -> str:
    return "".join(
        c for c in to_ascii(name).upper().replace("-", " ") if c in _SINGLE or c == " "
    ).strip()


#: The nations' names as the cartridge draws them, by team.
NATIONS = (
    "ITALY HOLLAND ENGLAND NORWAY SPAIN IRELAND PORTUGAL DENMARK GERMANY FRANCE BELGIUM SWEDEN "
    "ROMANIA BULGARIA RUSSIA SWISS GREECE CROATIA AUSTRIA WALES SCOTLAND N.IRELAND CZECH POLAND "
    "JAPAN S.KOREA TURKEY NIGERIA CAMEROON MOROCCO BRAZIL ARGENTINA COLUMBIA MEXICO USA URUGUAY"
).split()


def _cover(word: str) -> list[str]:
    """`word` cut into letters, spaces and font pairs, fewest sprites first."""
    best: dict[int, tuple[int, list[str]]] = {len(word): (0, [])}
    for i in range(len(word) - 1, -1, -1):
        cost, rest = best[i + 1]
        options = [(cost + (0 if word[i] == " " else 2), [word[i], *rest])]
        if word[i : i + 2] in _PAIRS:
            cost, rest = best[i + 2]
            options.append((cost + 1, [word[i : i + 2], *rest]))
        best[i] = min(options, key=lambda o: o[0])
    return best[0][1]


#: A blank OBJ tile: an empty name is one sprite of it, since an entry of no
#: sprites ends the unpacking (`$80:94C9`) and leaves the rest unloaded.
BLANK = 0xAF


def sprites(word: str) -> list[Sprite]:
    """`word` centred on x 40, right to left as the cartridge lists them."""
    if not word.strip():
        return [(CENTRE, 4, BLANK, 0x0A)]
    out: list[Sprite] = []
    x = 0
    for part in _cover(word):
        if part == " ":
            pass
        elif len(part) == 2:
            out.append((x + 4, 8, _PAIRS[part], 0x1A))  # 16x16 draws 4 up and left
        else:
            t = _SINGLE[part]
            out += [(x, 4, t, 0x0A), (x, 12, t + 0x10, 0x0A)]
        x += 8 * len(part)
    left = CENTRE - x // 2
    return [(left + sx, y, t, a) for sx, y, t, a in reversed(out)]


def candidates(*names: str) -> list[str]:
    """What to draw, best first: each name that fits 9 letters, the part
    before a hyphen, then the first cut shorter and shorter."""
    texts = [t for t in (text(n) for n in names) if t]
    if not texts:
        return []
    before_hyphen = text(names[0].split("-")[0]) if "-" in names[0] else ""
    out = [t for t in [*texts, before_hyphen] if t and len(t) <= MAX_LETTERS]
    cut = texts[-1][:MAX_LETTERS].rstrip()
    while len(cut) >= 3:
        out.append(cut)
        cut = cut[:-1].rstrip()
    return list(dict.fromkeys(out))


def _source(image: bytes | bytearray) -> int:
    address = int.from_bytes(image[SCRIPTS[0] : SCRIPTS[0] + 3], "little")
    return (address >> 16 & 0x7F) * 0x8000 + (address & 0x7FFF)


def read(image: bytes | bytearray) -> list[list[Sprite]]:
    """Each team's sprites, by team."""
    raw = decompress(bytes(image), _source(image))
    n, total = raw[0], struct.unpack_from("<H", raw, 1)[0]
    counts = list(raw[3 : 3 + n])
    blocks = [raw[3 + n + k * total : 3 + n + (k + 1) * total] for k in range(4)]
    entries, at = [], 0
    for c in counts:
        entries.append([tuple(b[at + i] for b in blocks) for i in range(c)])
        at += c
    start, order = FIRST, {}
    for k, c in enumerate(counts):
        order[start] = k
        start += 2 * c
    pointers = struct.unpack_from(f"<{TEAMS}H", image, POINTERS)
    return [[_signed(s) for s in entries[order[p]]] for p in pointers]


def _signed(s: tuple[int, ...]) -> Sprite:
    x, y, t, a = s
    return (x - 256 if x > 127 else x, y - 256 if y > 127 else y, t, a)


def write(image: bytearray, names: dict[int, list[str]]) -> dict[int, str]:
    """Give each team in `names` the first of its candidates (`candidates`)
    the sprites allow: every club starts with its best, then the costliest
    steps down until the total fits. Returns what each team got."""
    teams = read(image)
    # The nations left drawn in fewer sprites where the font's pairs allow.
    for t, nation in enumerate(NATIONS):
        if t not in names and len(sprites(nation)) < len(teams[t]):
            teams[t] = sprites(nation)
    chosen = {t: 0 for t, c in names.items() if c}
    free = SPRITES - sum(len(s) for t, s in enumerate(teams) if t not in chosen)

    def cost(t: int) -> int:
        return len(sprites(names[t][chosen[t]]))

    while sum(map(cost, chosen)) > free:
        shorter = [t for t in chosen if chosen[t] + 1 < len(names[t])]
        if not shorter:
            break
        chosen[max(shorter, key=lambda t: (cost(t), -t))] += 1
    for t in chosen:
        teams[t] = sprites(names[t][chosen[t]])
    while sum(map(len, teams)) > SPRITES:  # names too few letters to cut: blank one
        t = max(chosen, key=lambda t: len(teams[t]))
        teams[t] = sprites("")
    counts = [len(s) for s in teams]
    flat = [s for e in teams for s in e]
    raw = bytes([TEAMS]) + struct.pack("<H", len(flat)) + bytes(counts)
    for k in range(4):
        raw += bytes(s[k] & 0xFF for s in flat)
    stream = compress_large(raw)
    start, end = ROOM
    if len(stream) > end - start:
        raise ValueError(f"The big team names need {len(stream)} bytes; {end - start} free")
    image[start:end] = b"\xff" * (end - start)
    image[start : start + len(stream)] = stream
    pointer = (BANK_AF << 16 | 0x8000 | start & 0x7FFF).to_bytes(3, "little")
    for at in SCRIPTS:
        image[at : at + 3] = pointer
    at = FIRST
    for t, c in enumerate(counts):
        struct.pack_into("<H", image, POINTERS + 2 * t, at)
        at += 2 * c
    return {t: names[t][chosen[t]] for t in chosen}
