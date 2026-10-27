"""Team flags: 24x16, three colours, drawn on the scoreboard and team select.

Checked in Mesen 2 on the USA cartridge:

* $81:E730 (ROM 0xE730) holds two 16-bit pointers per team, the flag's top
  and bottom halves, each a Konami stream (`iss_snes.konami`) of three 4bpp
  tiles. Bit 15 of a stream's header marks tiles stored plane by plane (eight
  bytes of plane 0, eight of plane 1, then planes 2 and 3 as usual), which
  the game interleaves; without it the tiles are as VRAM holds them.
* The bank comes from the DMA templates that load a flag (`TEMPLATES`, bank
  $A7 on the cartridge, where the flags fill 0x13E4B6-0x13EC14 with 85 bytes
  to spare). `write` moves every flag to the 17 KiB bank $AF leaves free
  (`ROOM`) and points the templates there.
* The cup and league screens draw flags from RAM: $82:9021 (`RAM_LIST`) loads
  up to 64 halves into $7F:0A80-$7F:227F, 0x60 bytes each, and $82:F829
  (`RAM_CELLS`) gives each team the cell of its top half (the bottom follows).
  `write` rebuilds both so every distinct flag has its own cell pair.
* $81:E7D8 (ROM 0xE7D8) points each team at five words in bank $89: a
  header, then OBJ entries 12-15. A flag pixel is 12 + its value; entry 12 is
  the shirt numbers' in a match (`kits.NUMBERS`), so a flag here draws in 13-15
  only. The chest designs (`kits`) draw in the same entries.
"""

from __future__ import annotations

import struct
from collections import Counter
from itertools import permutations

from ...core.errors import RomError
from ..iss_snes.konami import compress, decompress
from ..iss_snes.models import Flag
from ..iss_snes.rom_writer import FLAG_HEIGHT, _distance, _flag_half_tiles, _half_rows, _pinned

POINTERS = 0xE730
COLOURS = 0xE7D8
BANK_89 = 0x48000
BANK_AF = 0xAF
FREE = (0x17BD1E, 0x17BD1E + 17122)  # $AF:BD1E, free on the cartridge
ROOM = (FREE[0], FREE[1] - 0x2000)  # the rest: `pages`, `names`, `bignames`
#: (template, entries): each a word, then entries of a word and a 24-bit
#: source whose bank byte is the flags' bank.
TEMPLATES = ((0xE715, 4), (0x177A4, 4), (0xEB80, 2), (0x1156E, 2))
RAM_LIST = 0x11021
RAM_CELLS = 0x17829
RAM_FIRST, RAM_HALF, RAM_HALVES = 0x0A80, 0x60, 64
TEAMS = 42  # the 36 nations and the six extra teams, all with flags
RGB = tuple[int, int, int]


def _rgb(word: int) -> RGB:
    r, g, b = (word >> s & 31 for s in (0, 5, 10))
    return r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2


def _word(c: RGB) -> int:
    return c[0] >> 3 | (c[1] >> 3) << 5 | (c[2] >> 3) << 10


def colour_at(image: bytes | bytearray, team: int) -> int:
    """Where `team`'s five flag words start."""
    return BANK_89 + (struct.unpack_from("<H", image, COLOURS + 2 * team)[0] & 0x7FFF)


def colours(image: bytes | bytearray, team: int) -> list[RGB]:
    """Entries 12-15 as the flag palette holds them."""
    return [_rgb(w) for w in struct.unpack_from("<4H", image, colour_at(image, team) + 2)]


def write_colours(image: bytearray, team: int, entries: list[RGB]) -> None:
    """Entries 13-15 (`entries[1:]`); the header and entry 12 stay."""
    struct.pack_into("<3H", image, colour_at(image, team) + 4, *map(_word, entries[1:4]))


def _bank(image: bytes | bytearray) -> int:
    """The file offset of the bank the flags live in."""
    return (image[TEMPLATES[0][0] + 6] & 0x7F) * 0x8000


def _halves(image: bytes | bytearray, team: int) -> list[bytes]:
    out = []
    bank = _bank(image)
    for h in range(2):
        at = bank + (struct.unpack_from("<H", image, POINTERS + 4 * team + 2 * h)[0] & 0x7FFF)
        out.append(bytes(image[at : at + (struct.unpack_from("<H", image, at)[0] & 0x7FFF)]))
    return out


def _tiles(blob: bytes) -> bytes:
    """A half's three tiles as VRAM holds them."""
    raw = decompress(blob)
    if not blob[1] & 0x80:
        return raw
    out = bytearray(raw)
    for t in range(3):
        for r in range(8):
            out[32 * t + 2 * r] = raw[32 * t + r]
            out[32 * t + 2 * r + 1] = raw[32 * t + 8 + r]
    return bytes(out)


def _blob(rows: list[list[int]]) -> bytes:
    """The smaller of the two ways to store a half (values 0-3 = entries 12-15)."""
    tiles = _flag_half_tiles(rows)
    planar = bytearray(tiles)
    for t in range(3):
        for r in range(8):
            planar[32 * t + r] = tiles[32 * t + 2 * r]
            planar[32 * t + 8 + r] = tiles[32 * t + 2 * r + 1]
    flat, split = compress(tiles), bytearray(compress(bytes(planar)))
    split[1] |= 0x80
    return min(flat, bytes(split), key=len)


def read(image: bytes | bytearray, team: int) -> Flag:
    rows = [r for blob in _halves(image, team) for r in _half_rows(_tiles(blob))]
    return Flag(rows, colours(image, team))


def blobs(flag: Flag) -> tuple[bytes, bytes]:
    half = FLAG_HEIGHT // 2
    return _blob(flag.pixels[:half]), _blob(flag.pixels[half:])


def _gap(flag: Flag, a: int, b: int) -> int:
    return _distance(flag.colours[a], flag.colours[b])


def three_colours(flag: Flag) -> Flag:
    """`flag` on entries 13-15 only: its rarest colours painted over in their
    nearest until three are left, then the order that packs smallest."""
    counts = Counter(v for row in flag.pixels for v in row)
    where = {v: v for v in counts}
    while len(set(where.values())) > 3:
        left = Counter(where[v] for v in where for _ in range(counts[v]))
        rare = min(left, key=lambda v: (left[v], v))
        near = min(
            (v for v in left if v != rare),
            key=lambda v: _distance(flag.colours[v], flag.colours[rare]),
        )
        where = {k: near if w == rare else w for k, w in where.items()}
    kept = sorted(set(where.values()))
    best: Flag | None = None
    for spots in permutations((1, 2, 3), len(kept)):
        to = dict(zip(kept, spots, strict=True))
        cols = [flag.colours[0]] * 4
        for v, s in to.items():
            cols[s] = flag.colours[v]
        for s in (1, 2, 3):
            if s not in to.values():
                cols[s] = cols[spots[0]]
        f = Flag([[to[where[v]] for v in row] for row in flag.pixels], cols)
        if best is None or sum(map(len, blobs(f))) < sum(map(len, blobs(best))):
            best = f
    assert best is not None
    return best


def two_colours(flag: Flag) -> Flag | None:
    """`flag` with its rarest colour painted over in its nearest; None when
    that would leave one colour."""
    counts = Counter(v for row in flag.pixels for v in row)
    if len(counts) < 3:
        return None
    rare = min(counts, key=lambda v: (counts[v], v))
    near = min(
        (v for v in counts if v != rare),
        key=lambda v: _distance(flag.colours[v], flag.colours[rare]),
    )
    return Flag([[near if v == rare else v for v in row] for row in flag.pixels], flag.colours)


def write(
    image: bytearray,
    flags: dict[int, Flag],
    fallbacks: dict[int, Flag],
    pins: dict[int, dict[int, tuple[RGB, int]]] | None = None,
    blank: list[int] | None = None,
) -> dict[int, str]:
    """Give each team in `flags` its flag (`three_colours` first) and its
    colours; every other team keeps its own blobs. When the rooms are full the
    largest new flag loses a colour, then gives way to its fallback (a
    two-band flag). Then a team in `pins` has its entries 13-15 reordered
    (`iss_snes`'s `_pinned`) so its chest design shows the colours it asks
    for, unless that no longer fits. A team in `blank` gets a transparent
    flag. Returns what gave way, {team: note}."""
    chosen = {t: three_colours(f) for t, f in flags.items()}
    current = {t: tuple(_halves(image, t)) for t in range(TEAMS)}
    clear = compress(bytes(96))
    for t in blank or []:
        current[t] = (clear, clear)
    notes: dict[int, str] = {}

    def of(team: int) -> tuple[bytes, ...]:
        return blobs(chosen[team]) if team in chosen else current[team]

    def layout() -> dict[bytes, int] | None:
        """Where each distinct blob goes, first fit in team order; None when full."""
        where: dict[bytes, int] = {}
        spots = [list(ROOM)]
        for team in range(TEAMS):
            for blob in of(team):
                if blob in where:
                    continue
                spot = next((s for s in spots if s[1] - s[0] >= len(blob)), None)
                if spot is None:
                    return None
                where[blob] = spot[0]
                spot[0] += len(blob)
        return where

    stuck: set[int] = set()
    while (where := layout()) is None:
        crests = [t for t in sorted(chosen, key=lambda t: -sum(map(len, of(t)))) if t not in stuck]
        if not crests:
            raise RomError("The flags do not fit in bank $AF")
        team = crests[0]
        simpler = two_colours(chosen[team])
        if simpler is not None and team not in notes:
            chosen[team] = three_colours(simpler)
            notes[team] = "simplified to 2 colours to fit"
        elif team in fallbacks:
            chosen[team] = three_colours(fallbacks.pop(team))
            notes[team] = "did not fit; two-band flag instead"
        else:
            stuck.add(team)

    for team, want in sorted((pins or {}).items()):
        before = chosen.get(team)
        chosen[team] = _pinned(before or read(image, team), want)
        if (fits := layout()) is None:
            if before is None:
                del chosen[team]
            else:
                chosen[team] = before
            notes[team] = "kept its colour order; the chest design's would not fit"
        else:
            where = fits

    start, end = ROOM
    image[start:end] = b"\xff" * (end - start)
    for blob, at in where.items():
        image[at : at + len(blob)] = blob
    for team in range(TEAMS):
        for h, blob in enumerate(of(team)):
            pointer = 0x8000 | where[blob] & 0x7FFF
            struct.pack_into("<H", image, POINTERS + 4 * team + 2 * h, pointer)
    for at, entries in TEMPLATES:
        for k in range(entries):
            image[at + 2 + 5 * k + 4] = BANK_AF
    for team, note in _ram_cells(image, [of(t) for t in range(TEAMS)], where, set(chosen)).items():
        notes.setdefault(team, note)
    for team, flag in chosen.items():
        write_colours(image, team, flag.colours)
    return notes


def _ram_cells(
    image: bytearray, halves: list[tuple[bytes, ...]], where: dict[bytes, int], first: set[int]
) -> dict[int, str]:
    """Rebuild the RAM load list and each team's cell: one cell pair per
    distinct flag, the teams in `first` served first. A team past the 32 pairs
    the RAM holds borrows the first pair (its flag is wrong on the cup and
    league screens). Returns those teams, {team: note}."""
    pairs: dict[tuple[bytes, ...], int] = {}
    notes: dict[int, str] = {}
    order = sorted(range(TEAMS), key=lambda t: (t not in first, t))
    for team in order:
        if halves[team] not in pairs and len(pairs) < RAM_HALVES // 2:
            pairs[halves[team]] = RAM_FIRST + 2 * RAM_HALF * len(pairs)
    entries = bytearray(struct.pack("<H", 1))
    for pair, cell in pairs.items():
        for h, blob in enumerate(pair):
            source = BANK_AF << 16 | 0x8000 | where[blob] & 0x7FFF
            entries += (0x7F0000 | cell + RAM_HALF * h).to_bytes(3, "little")
            entries += source.to_bytes(3, "little")
    entries += b"\xff\xff"
    image[RAM_LIST : RAM_LIST + len(entries)] = entries
    for team in range(TEAMS):
        if halves[team] not in pairs:
            notes[team] = "shows another flag on the cup screens; their RAM is full"
        struct.pack_into("<H", image, RAM_CELLS + 2 * team, pairs.get(halves[team], RAM_FIRST))
    return notes
