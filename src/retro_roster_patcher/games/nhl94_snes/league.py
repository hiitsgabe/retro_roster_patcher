"""The cartridge laid out as 32 clubs.

The 26 clubs stay in their slots, Seattle and Vegas keep the All-Star slots 26
and 27, and Columbus, Minnesota, Nashville and Utah take four new ones, 28-31.
Every per-team table the game reads (found through the NHL94SNESVault and
NHL94SNES disassemblies, both public domain, and checked in mednafen) grows
to 32:

* Read with a long address (`LDA.L table,X`): center ice $9C:83B7, the logo
  palettes $9C:8497 (32 teams, then the NHL shield and the NHLPA logo), the
  banner palettes $9C:850F, the photo table $9D:CD53 (26 a team), the songs
  $80:DA56 (six long pointers a home team; $FFFFFFFF plays a random one). Each
  moves to `TABLES`, a bank of the grown image, and its loads are repointed.
* Read as a bank and a 16-bit base (`ADC #base` and `LDA #bank`): the photo
  palettes $9D:D8B3 and the home and away uniforms $96:CD4E and $96:C9CE.
  They move to `TABLES` too.
* Grown where they are: the team list $9C:A5E7 (whose new entries overlap the
  start of All-Stars East's block, moved first into the padding that ends bank
  $9C) and the banner names $9C:9649 (16-bit pointers into bank $9C, the
  names moved past the longer table).
* The logo archive gets 34 entries in the same order.
* Counts: Game Setup's Home and Away options and its four bound checks go to
  32, as does the playoff modes' Team option, and New Playoffs' seed lookup
  stops capping the user's team at 25.

A new slot starts as a copy of slot 26 until a patch names it. `expand` is
idempotent; an image that is neither the cartridge's layout nor this one is
left alone.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ...formats import ea_huff, snes
from ...formats.ea_tdb import refpack_compress, refpack_decompress

RETAIL_SLOTS = 28
SLOTS = 32
#: The cartridge slot each new one starts from.
TEMPLATE = 26
TWO_MIB = 0x200000
#: The bank the moved tables live in (file offsets, headerless).
TABLES = range(0x158000, 0x160000)
BANK = 0x8000


def _file(cpu: int) -> int:
    return (cpu >> 16 & 0x7F) * BANK + (cpu & 0x7FFF)


def _cpu(offset: int) -> int:
    return 0x800000 | (offset // BANK) << 16 | 0x8000 | offset % BANK


def _u16(rom: bytes | bytearray, at: int) -> int:
    return rom[at] | rom[at + 1] << 8


def _long(rom: bytes | bytearray, at: int) -> int:
    return rom[at] | rom[at + 1] << 8 | rom[at + 2] << 16


@dataclass(frozen=True)
class Table:
    """A per-team table: where it is on the cartridge (CPU address), its
    entry size and count there and here, and where it moves (file offset)."""

    retail: int
    size: int
    entries: int
    at: int
    #: (CPU address of an `LDA.L table+k,X`, k).
    longs: tuple[tuple[int, int], ...] = ()
    #: (CPU address of `ADC #base`, CPU address of `LDA #bank`).
    bases: tuple[tuple[int, int], ...] = ()
    #: Entries past the cartridge's, which the first `extra` of come after the
    #: teams (the logo palettes' NHL shield and NHLPA logo).
    extra: int = 0

    @property
    def cpu(self) -> int:
        return _cpu(self.at)


CENTER_ICE = Table(
    0x9C83B7,
    8,
    SLOTS,
    TABLES.start,
    longs=(
        (0x9BC183, 0), (0x9FE838, 0), (0x9DBB6F, 0), (0x9BC189, 2), (0x9DBB75, 2),
        (0x9FE832, 2), (0x9DBB35, 4), (0x9BC1F4, 4), (0x9BC1FA, 6), (0x9DBB3B, 6),
    ),
)  # fmt: skip
LOGO_PALETTES = Table(
    0x9C8497,
    4,
    SLOTS + 2,
    TABLES.start + 0x100,
    longs=(
        (0x9DDE0C, 0),
        (0x9DDEB6, 0),
        (0x9DDFD0, 0),
        (0x9DDE06, 2),
        (0x9DDEB0, 2),
        (0x9DDFCA, 2),
    ),
    extra=2,
)
BANNER_PALETTES = Table(
    0x9C850F,
    4,
    SLOTS,
    TABLES.start + 0x200,
    longs=(
        (0x9D8DAD, 0),
        (0x9DAEA1, 0),
        (0x9FBE76, 0),
        (0x9D8DA7, 2),
        (0x9DAE9B, 2),
        (0x9FBE70, 2),
    ),
)
SONGS = Table(0x80DA56, 24, SLOTS, TABLES.start + 0x300, longs=((0x9C9B7F, 0), (0x9C9B77, 2)))
PHOTO_PALETTES = Table(
    0x9DD8B3, 4, SLOTS, TABLES.start + 0x700, bases=((0x9DCA8D, 0x9DCA92), (0x9DCC0C, 0x9DCC11))
)
HOME_UNIFORMS = Table(0x96CD4E, 32, SLOTS, TABLES.start + 0x800, bases=((0x9FB925, 0x9FB92A),))
AWAY_UNIFORMS = Table(0x96C9CE, 32, SLOTS, TABLES.start + 0xC00, bases=((0x9FB95A, 0x9FB95F),))
#: 26 entries a team.
PHOTOS = Table(
    0x9DCD53,
    4 * 26,
    SLOTS,
    TABLES.start + 0x1000,
    longs=((0x9DC9DD, 0), (0x9DCC65, 0), (0x9DC9E3, 2), (0x9DCC6B, 2)),
)
#: The playoff tree's two colour slots a team (offsets into BG palettes 4-7),
#: read by the loop at $9D:8D9F that colours every team's bracket tiles.
BRACKET_COLOURS = Table(
    0x9D93AD, 2, SLOTS, TABLES.start + 0x1D80, longs=((0x9D8DBD, 0), (0x9D8DDC, 0))
)
#: Every slot of those four palettes is taken but colour 1 (the yellow border)
#: and the last two: Vegas, which the cartridge gave none (its All-Stars West
#: never reached a playoff), gets those; each new club shares the slots of a
#: club of like colours, which it then shows.
BRACKET_SHARES = {27: None, 28: 14, 29: 5, 30: 2, 31: 26}
MOVED = (
    CENTER_ICE,
    LOGO_PALETTES,
    BANNER_PALETTES,
    SONGS,
    PHOTO_PALETTES,
    HOME_UNIFORMS,
    AWAY_UNIFORMS,
    PHOTOS,
    BRACKET_COLOURS,
)

TEAM_LIST = 0x9CA5E7
BANNER_POINTERS = 0x9C9649
BANNER_LIST_END = 0x9C9786
#: Where the banner names start once the pointers are 32.
BANNER_LIST = BANNER_POINTERS + 2 * SLOTS
#: The padding that ends bank $9C, where All-Stars East's block moves.
BANK_9C_PADDING = 0x9CF945
BANK_9C_END = 0x9D0000

LOGO_LOADERS = (0x9D87DB, 0x9DC69B, 0x9DD9B7)
#: The menus put the NHL shield or the NHLPA logo in a logo box by archive
#: index (`LDA #$1C` / `#$1D`, `STA $7E:3454` / `3452`), Columbus's and
#: Minnesota's in the 34-entry archive; they follow the two past the teams.
#: Not in `CODE`, so an image an earlier patch grew is still `expanded` and
#: gets them on its next patch.
LOGO_BOXES = (
    0x9BC25A,
    0x9BC261,
    0x9BC83B,
    0x9BC842,
    0x9C8593,
    0x9D9D41,
    0x9DA4B4,
    0x9DAA95,
    0x9DAAD4,
)
ARCHIVE_SIZE = 0x7C00

#: (CPU address, cartridge bytes, expanded bytes).
CODE = (
    (0x9DACD3, "1c001c00", "20002000"),
    (0x9DAB37, "c91c00", "c92000"),
    (0x9DAB55, "c91c00", "c92000"),
    (0x9DABE2, "c91c00", "c92000"),
    (0x9DABF8, "c91c00", "c92000"),
    # New Playoffs and Best of 7 offer 26 teams.
    (0x9DA912, "a91a00", "a92000"),
    (0x9D98FD, "c91a00", "c92000"),
    # The playoff tree colours 32 teams' tiles; the Stanley Cup's move past them.
    (0x9D8D9F, "a21b00", "a21f00"),
    (0x9D8DFA, "697000", "698000"),
    (0x9D8E0B, "a2000e", "a20010"),
    # Their tiles, 4 a team, are built at $7F:6700 right below the colour
    # buffer at $7F:7500; 32 teams' start $200 lower, free once the menu
    # tiles decompressed there have gone to VRAM.
    (0x9D8E06, "a90067", "a90065"),
    *(
        (site, "9f00677f", "9f00657f")
        for site in (
            0x9D9439,
            0x9D9441,
            0x9D944E,
            0x9D9456,
            0x9D9467,
            0x9D946F,
            0x9D947C,
            0x9D9484,
            0x9D94A3,
            0x9D94AB,
            0x9D94B8,
            0x9D94C0,
            0x9D94D1,
            0x9D94D9,
            0x9D94E6,
            0x9D94EE,
        )
    ),  # fmt: skip
    (0x9D9902, "a91900", "a91f00"),
)


def _sites(table: Table, cpu: int) -> list[bytes]:
    """What each of the table's loads holds when it names `cpu` (its retail
    address or its new one)."""
    out = [(cpu + k).to_bytes(3, "little") for _, k in table.longs]
    for _ in table.bases:
        out += [(cpu & 0xFFFF).to_bytes(2, "little"), bytes([cpu >> 16, 0])]
    return out


def _operands(table: Table) -> list[int]:
    """The file offsets of the table's load operands, in `_sites` order."""
    out = [_file(site) + 1 for site, _ in table.longs]
    for adc, lda in table.bases:
        out += [_file(adc) + 1, _file(lda) + 1]
    return out


def _state(rom: bytes | bytearray) -> str | None:
    states = set()
    for cpu, old, new in CODE:
        here = bytes(rom[_file(cpu) : _file(cpu) + len(old) // 2]).hex()
        states.add("retail" if here == old else "expanded" if here == new else None)
    for table in MOVED:
        for at, retail, moved in zip(
            _operands(table), _sites(table, table.retail), _sites(table, table.cpu), strict=True
        ):
            site = bytes(rom[at : at + len(retail)])
            states.add("retail" if site == retail else "expanded" if site == moved else None)
    return states.pop() if len(states) == 1 else None


def expanded(rom: bytes | bytearray) -> bool:
    """Is `rom` (headerless) laid out as 32 clubs?"""
    return len(rom) >= TWO_MIB and _state(rom) == "expanded"


def _block_end(rom: bytes | bytearray, base: int) -> int:
    """The end of the team block at file offset `base`: its strings come last."""
    bank = base - base % BANK
    at = bank + (base % BANK + _u16(rom, base + 4)) % BANK
    for _ in range(4):
        at += _u16(rom, at)
    return at


def _decode(stream: bytes) -> bytes:
    if stream[:2] == ea_huff.MAGIC:
        return ea_huff.decompress(stream)
    return refpack_decompress(stream)


def archive_entries(archive: bytes, count: int) -> list[bytes]:
    """The logo archive's `count` entries: a table of u32 offsets, then the
    entries, which several offsets may share. The zeros padding the archive to
    its buffer are left off the last; every build pads it again."""
    offsets = struct.unpack_from(f"<{count}I", archive)
    archive = archive.rstrip(b"\0")
    ends = sorted({*offsets, len(archive)})
    return [archive[o : ends[ends.index(o) + 1]] for o in offsets]


def build_archive(entries: list[bytes]) -> bytes:
    """A logo archive of `entries`, each distinct one stored once."""
    out = bytearray(4 * len(entries))
    where: dict[bytes, int] = {}
    for i, entry in enumerate(entries):
        if entry not in where:
            where[entry] = len(out)
            out += entry
        struct.pack_into("<I", out, 4 * i, where[entry])
    return bytes(out)


def expand(rom: bytearray) -> bool:
    """Lay `rom` (headerless, in place) out as 32 clubs, growing it to 2 MiB.
    True when it is so laid out, now or by an earlier patch; False, writing
    nothing, for an image laid out otherwise."""
    if expanded(rom):
        _point_logo_boxes(rom)
        return True
    if _state(rom) != "retail":
        return False
    snes.grow(rom, TWO_MIB)
    if any(b != 0xFF for b in rom[TABLES.start : TABLES.stop]):
        return False
    padding = _file(BANK_9C_PADDING)
    table = _file(TEAM_LIST)
    grown = range(table + 4 * RETAIL_SLOTS, table + 4 * SLOTS)
    # The game reads a 16-bit pointer and supplies bank $9C itself.
    bank = table - table % BANK
    bases = [bank + (_u16(rom, table + 4 * s) & 0x7FFF) for s in range(RETAIL_SLOTS)]
    # Blocks the longer list overlaps (on the cartridge, All-Stars East's) move
    # into the padding that ends bank $9C.
    moving = sorted({b for b in bases if b < grown.stop and _block_end(rom, b) > grown.start})
    size = sum(_block_end(rom, b) - b for b in moving)
    if padding + size > _file(BANK_9C_END) or any(b != 0xFF for b in rom[padding : padding + size]):
        return False

    # Each table and its loads.
    for t in MOVED:
        src = _file(t.retail)
        teams = RETAIL_SLOTS + t.extra
        rows = [bytes(rom[src + t.size * s : src + t.size * (s + 1)]) for s in range(teams)]
        new = rows[:RETAIL_SLOTS] + [rows[TEMPLATE]] * (SLOTS - RETAIL_SLOTS) + rows[RETAIL_SLOTS:]
        if t is SONGS:
            new[RETAIL_SLOTS:SLOTS] = [b"\xff" * t.size] * (SLOTS - RETAIL_SLOTS)
        if t is BRACKET_COLOURS:
            for slot, partner in BRACKET_SHARES.items():
                new[slot] = b"\x7c\x7e" if partner is None else new[partner]
        rom[t.at : t.at + t.size * len(new)] = b"".join(new)
        for at, value in zip(_operands(t), _sites(t, t.cpu), strict=True):
            rom[at : at + len(value)] = value
    for cpu, _old, code in CODE:
        rom[_file(cpu) : _file(cpu) + len(code) // 2] = bytes.fromhex(code)

    # The overlapped blocks out of the way of the longer team list.
    moves, at = {}, padding
    for base in moving:
        block = bytes(rom[base : _block_end(rom, base)])
        rom[at : at + len(block)] = block
        rom[base : base + len(block)] = b"\xff" * len(block)
        moves[base], at = at, at + len(block)
    for slot in range(SLOTS):
        source = bases[slot if slot < RETAIL_SLOTS else TEMPLATE]
        at = table + 4 * slot
        rom[at : at + 4] = _cpu(moves.get(source, source)).to_bytes(3, "little") + b"\0"

    # The banner names past the longer pointer table.
    pointers = _file(BANNER_POINTERS)
    bank = pointers - pointers % BANK
    names = [
        bytes(rom[bank + _u16(rom, pointers + 2 * s) - 0x8000 :].split(b"\0", 1)[0])
        for s in range(RETAIL_SLOTS)
    ]
    names += [names[TEMPLATE]] * (SLOTS - RETAIL_SLOTS)
    at, where = _file(BANNER_LIST), {}
    for name in dict.fromkeys(names):
        where[name] = at
        rom[at : at + len(name) + 1] = name + b"\0"
        at += len(name) + 1
    if at > _file(BANNER_LIST_END):
        raise AssertionError("the cartridge's banner names fit, shared")
    rom[at : _file(BANNER_LIST_END)] = b"\0" * (_file(BANNER_LIST_END) - at)
    for slot, name in enumerate(names):
        struct.pack_into("<H", rom, pointers + 2 * slot, 0x8000 + where[name] - bank)

    # The logo archive: the 28 teams, the new ones, the NHL shield and NHLPA logo.
    site = _file(LOGO_LOADERS[0])
    current = rom[site + 1] << 16 | _u16(rom, site + 6)
    archive = _decode(bytes(rom[_file(current) :]))
    count = RETAIL_SLOTS + 2
    entries = archive_entries(archive, count)
    # The new slots and Vegas share All-Stars East's logo until a patch draws
    # theirs: four more offsets leave no room for more.
    entries = entries[: TEMPLATE + 1] + [entries[TEMPLATE]] * (SLOTS - TEMPLATE - 1) + entries[-2:]
    out = build_archive(entries)
    if len(out) > ARCHIVE_SIZE:
        raise AssertionError("the cartridge's logos fit with five shared")
    stream = refpack_compress(out.ljust(ARCHIVE_SIZE, b"\0"))
    at = TABLES.start + 0x1E00
    if at + len(stream) > TABLES.stop:
        raise AssertionError("the logo archive fits the tables' bank")
    rom[at : at + len(stream)] = stream
    for loader in LOGO_LOADERS:
        site = _file(loader)
        rom[site + 1] = _cpu(at) >> 16
        rom[site + 6 : site + 8] = (_cpu(at) & 0xFFFF).to_bytes(2, "little")
    _point_logo_boxes(rom)
    return True


def _point_logo_boxes(rom: bytearray) -> None:
    for cpu in LOGO_BOXES:
        at = _file(cpu) + 1
        if rom[at - 1] == 0xA9 and rom[at] in (RETAIL_SLOTS, RETAIL_SLOTS + 1):
            rom[at] += SLOTS - RETAIL_SLOTS
