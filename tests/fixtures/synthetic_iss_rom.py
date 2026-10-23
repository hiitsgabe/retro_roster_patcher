"""Fabricate a structurally valid International Superstar Soccer (SNES) ROM in memory.

Nothing here is derived from a real ROM. Every byte is computed from the format
`games/iss_snes/rom_reader.py` and `rom_writer.py` document.

The image is 1 MB because `ISSRomReader.validate_rom` accepts a floor of
1 048 576 bytes, the 8 Mbit size its own constants name as the USA/EUR release.
`rom_writer.MIN_PATCHABLE_SIZE` is 296 140, so the two guards are three and a
half times apart and a test can sit between them: `SIZE_ARITHMETIC_MINIMUM` below
is an image `data_fits` accepts and `validate_rom` refuses.

Every offset, the two team orders, the character encoding and the three pointer
formats are retranscribed here rather than imported from `games.iss_snes`, so
that moving `_OFS_PLAYER_NAMES`, swapping Scotland back to index 5 of the name
order, or dropping the 0x80 bias out of `_encode_p40000` breaks tests.
`encode_char` is written as the *rule* the ROM font encodes -- space at 0x00,
punctuation at six scattered values, the digits from 0x62, upper case from 0x6C
and lower case from 0x86 -- rather than as a copy of the table.

`player_name(team, slot)` is `T00P00` through `T26P14` and every byte of the
6-byte data record varies with both, so a writer that ignored the team index, or
used the enum order where the names use the name order, cannot satisfy an
equality. `player_name` is indexed by *name-order* position while
`player_data_record` is indexed by *enum-order* position: slot 5 is Scotland in
one and Wales in the other.

The filler is pseudo-random rather than zero because three writer methods
preserve bits they read back -- `write_player_data` keeps the high five bits of
two bytes, the high nibble of two more and three bits of the last, and the two
kit writers keep the eight words of hair and skin colour that follow the kit.
Against a zero-filled image each preservation is indistinguishable from a write
of zero.
"""

from __future__ import annotations

import pathlib
import struct

#: The 8 Mbit cartridge size `validate_rom` uses as its floor.
ROM_SIZE = 0x100000

#: The copier header a `.smc` carries in front of that.
SMC_HEADER_SIZE = 512

#: Smallest body `rom_writer.MIN_PATCHABLE_SIZE` accepts. Retranscribed as the
#: highest write the writer makes: the end of bank $89's flag room, which
#: `write_flags` rewrites whole.
SIZE_ARITHMETIC_MINIMUM = 0x48A80

TOTAL_TEAMS = 27
PLAYERS_PER_TEAM = 15

#: Independent transcription of `models.TEAM_ENUM_ORDER`.
TEAM_ENUM_ORDER = [
    "Germany",
    "Italy",
    "Holland",
    "Spain",
    "England",
    "Scotland",
    "Wales",
    "France",
    "Denmark",
    "Sweden",
    "Norway",
    "Ireland",
    "Belgium",
    "Austria",
    "Switz",
    "Romania",
    "Bulgaria",
    "Russia",
    "Argentina",
    "Brazil",
    "Colombia",
    "Mexico",
    "U.S.A.",
    "Nigeria",
    "Cameroon",
    "S.Korea",
    "Super Star",
]

#: Independent transcription of `models.TEAM_NAME_ORDER`. Scotland is at 24 here
#: and at 5 above; everything between shifts by one.
TEAM_NAME_ORDER = [
    "Germany",
    "Italy",
    "Holland",
    "Spain",
    "England",
    "Wales",
    "France",
    "Denmark",
    "Sweden",
    "Norway",
    "Ireland",
    "Belgium",
    "Austria",
    "Switz",
    "Romania",
    "Bulgaria",
    "Russia",
    "Argentina",
    "Brazil",
    "Colombia",
    "Mexico",
    "U.S.A.",
    "Nigeria",
    "Cameroon",
    "Scotland",
    "S.Korea",
    "Super Star",
]


OFS_PLAYER_NAMES = 0x3B62C
PLAYER_NAME_LENGTH = 8
OFS_PLAYER_DATA = 0x387EC
PLAYER_DATA_LENGTH = 6

OFS_KIT1_RANGE1 = 0x2EA3B
OFS_KIT1_RANGE2 = 0x2F0EB
OFS_KIT2_RANGE1 = 0x2ECBB
OFS_KIT2_RANGE2 = 0x2F1EB
OFS_GK_RANGE1 = 0x2EF37
OFS_GK_RANGE2 = 0x2F2E7
OUTFIELD_KIT_STRIDE = 32
GK_KIT_STRIDE = 24

OFS_FLAG_COLORS_RANGE1 = 0x2DD91
OFS_FLAG_COLORS_RANGE2 = 0x2DE4F
FLAG_COLORS_STEP = 10

OFS_PREDOMINANT_COLOR = 0x8DB2
OFS_FLAG_TILE_PTRS = 0x941A
FLAG_ROOM_START = 0x48000
FLAG_ROOM_END = 0x48A80

#: Each team's flag cell on the preload screens, and its pointer to a two-sprite
#: record in bank $88 (file 0x40000 + pointer - 0x8000).
OFS_FLAG_CELLS = 0x39D78
OFS_FLAG_SPRITES = 0x39DE4
FLAG_SPRITE_BASE = 0x44500
#: The eight teams the cartridge gives no cell ($FFFF) and the OBJ tile each gets.
OWN_FLAG_CELLS = {
    "Holland": 0x1A1,
    "England": 0x1A4,
    "Denmark": 0x1A7,
    "Norway": 0x1AA,
    "Ireland": 0x1AD,
    "Belgium": 0x103,
    "Austria": 0x106,
    "Russia": 0x109,
}

#: The chest-design table: 8 poses x 28 slots of 3-byte SNES addresses, each
#: of a 2-byte header and a 4bpp 8x8 tile. Every slot points at the blank
#: design but Germany (the band) and Austria and Argentina (the stripes).
OFS_CHEST_PTRS = 0x17153
CHEST_SLOTS = 28
CHEST_POSES = 8
BLANK_DESIGN = 0x8CD430
BAND_DESIGN = 0x9AA209
STRIPES_DESIGN = 0x9AA319
POSE_STRIDE = 0x286
#: The band's rows use entries 13, 14 and 15; the stripes' columns entry 13.
BAND_TILE = [[0] * 8, [13] * 8, [14] * 8, [15] * 8, [0] * 8, [0] * 8, [0] * 8, [0] * 8]
STRIPES_TILE = [[13, 0, 0, 13, 0, 0, 13, 0]] * 8

OFS_NAME_TILES_PTRS = 0x93CD
NAME_TILES_DISPLACED_BASE = 0x17680
NAME_TILES_DISPLACED_END = 0x18000
NAME_TILES_CAPACITY = NAME_TILES_DISPLACED_END - NAME_TILES_DISPLACED_BASE

OFS_DESC_PTRS = 0x38000
OFS_TEAM_NAME_TEXT_PTRS = 0x39DAE
MAX_NAME_TEXT_ADDR = 0x44478

#: The ten operand bytes `write_name_tiles` overwrites. Filled with 0x89 -- the
#: bank the unpatched game reads from -- so the write to 0x82 is observable.
DISPLACEMENT_PATCH_POINTS = (
    0x93C6,
    0x93CB,
    0x3A7EB,
    0x3A7F0,
    0x3A7F5,
    0x3A7FA,
    0x3A7FF,
    0x3A804,
    0x3A809,
    0x3A80E,
)
UNPATCHED_BANK_BYTE = 0x89
PATCHED_BANK_BYTE = 0x82


#: Where this fixture puts the 27 selection-screen name blobs. Below
#: `MAX_NAME_TEXT_ADDR`, so `write_team_name_texts` computes a positive budget.
NAME_TEXT_BASE = 0x43000
#: Entries in one blob. `1 + count * 4` bytes each, so 4 is 17 bytes.
NAME_TEXT_ENTRIES = 4


#: Where this fixture puts the 27 Konami-compressed tile blobs: right after the
#: flags, inside the flag room, as on the cartridge. `write_flags` refuses to
#: run until `write_name_tiles` has moved them out.
NAME_TILE_BASE = FLAG_ROOM_START + TOTAL_TEAMS * 2 * 6
NAME_TILE_BLOB_SIZE = 36


#: SNES bank $02: a pointer of `snes_addr` means file offset
#: `0x10000 + (snes_addr - 0x8000)`.
DESC_BANK_BASE = 0x10000
DESC_BANK_SNES_BASE = 0x8000
#: Where this fixture puts the 27 description blocks, as a file offset. Clear of
#: the displaced name-tile region at 0x17680.
DESC_BASE = 0x10100
DESC_STRIDE = 96
#: `FE` + a 16-byte formation line + `FE` + a space + `FD`, so `FD` is the
#: twentieth byte and the description text starts at the twenty-first.
DESC_HEADER = bytes([0xFE]) + bytes(range(0x30, 0x40)) + bytes([0xFE, 0x20, 0xFD])
DESC_TEXT_LENGTH = 60
DESC_TERMINATOR = 0xFF
DESC_LINE_WIDTH = 15

#: A description block planted one bank below where the table belongs, close
#: enough to 0x8DB2 that an unguarded write lands on the predominant-colour
#: table. `write_team_descriptions` computes `0x8000 + snes_addr`, so this
#: pointer names file offset 0x8DA0 and the text starts twenty bytes later.
LOW_BANK_DESC_SNES_POINTER = 0x0DA0
LOW_BANK_DESC_OFFSET = 0x8000 + LOW_BANK_DESC_SNES_POINTER


def encode_char(ch: str) -> int:
    """The ISS font encoding, stated as a rule rather than copied as a table.

    Space is 0x00 -- a SPACE and not a terminator -- six punctuation marks sit
    at scattered values, the digits run from 0x62, upper case from 0x6C and
    lower case from 0x86. Anything else is 0x00, which is why an accented name
    silently loses characters.
    """
    if ch == " ":
        return 0x00
    if ch == "-":
        return 0x53
    if ch == ".":
        return 0x54
    if ch == '"':
        return 0x56
    if ch == "'":
        return 0x5C
    if ch == "/":
        return 0x5F
    if "0" <= ch <= "9":
        return 0x62 + (ord(ch) - ord("0"))
    if "A" <= ch <= "Z":
        return 0x6C + (ord(ch) - ord("A"))
    if "a" <= ch <= "z":
        return 0x86 + (ord(ch) - ord("a"))
    return 0x00


def decode_char(value: int) -> str:
    """Inverse of `encode_char`. A byte with no glyph decodes to the empty string.

    Not `"?"`: `rom_reader.decode_iss_name` contributes nothing for a byte the
    table does not name, so a decoder that substituted a placeholder would
    disagree with it on exactly the images this fixture builds.
    """
    if value == 0x00:
        return " "
    if value == 0x53:
        return "-"
    if value == 0x54:
        return "."
    if value == 0x56:
        return '"'
    if value == 0x5C:
        return "'"
    if value == 0x5F:
        return "/"
    if 0x62 <= value <= 0x6B:
        return chr(ord("0") + value - 0x62)
    if 0x6C <= value <= 0x85:
        return chr(ord("A") + value - 0x6C)
    if 0x86 <= value <= 0x9F:
        return chr(ord("a") + value - 0x86)
    return ""


def encode_name(name: str, length: int = PLAYER_NAME_LENGTH) -> bytes:
    """Encode and pad a name to `length` bytes with 0x00, which is a space."""
    encoded = [encode_char(ch) for ch in name[:length]]
    encoded += [0x00] * (length - len(encoded))
    return bytes(encoded)


def decode_name(raw: bytes | bytearray) -> str:
    """Decode a padded name field and strip the padding."""
    return "".join(decode_char(b) for b in raw).strip()


def name_storage_index(enum_index: int) -> int:
    """Where slot `enum_index`'s player names live, by lookup in the two orders.

    Retranscribed from the *lists*, not imported from
    `models.name_storage_index`: a test that called that function to find the
    names it is checking that function placed would agree with any permutation
    of it.
    """
    return TEAM_NAME_ORDER.index(TEAM_ENUM_ORDER[enum_index])


def player_name(name_team: int, slot: int) -> str:
    """The name in one storage position, encoding both coordinates.

    Indexed by *name-order* team, because that is the order the ROM stores names
    in. Six characters in an eight-byte field, so the two padding bytes are part
    of what a test can assert on.
    """
    return f"T{name_team:02d}P{slot:02d}"


def player_data_record(enum_team: int, slot: int) -> bytes:
    """One 6-byte player-data record, indexed by *enum-order* team.

    Every byte varies with both coordinates, and every bit the writer must
    clear or keep is set: b1 bits 3 and 7 and b2 bit 3 (unknown, 0 in retail),
    b5's hair nibble and bits 4, 5 and 7 (written 0), and b5 bit 6 (the
    alternate palette, kept).
    """
    salt = enum_team * 17 + slot
    return bytes(
        [
            0x21 + (salt % 0x40),  # dribble | dash
            0xC8 | (salt % 8),  # kick | shooting, bits 3 and 7 set
            0xA8 | ((salt + 3) % 8),  # technique | stamina, bit 3 set
            0x50 | ((salt + 5) % 16),  # shirt number - 1 | ball_dash
            0x30 | ((salt + 7) % 16),  # defense | offense
            0xF0 | ((salt + 9) % 16),  # hair | palette bit 6, bits 7, 5, 4 set
        ]
    )


def encode_p40000(address: int) -> bytes:
    """P40000 pointer: 16-bit offset from 0x40000 with the high byte biased 0x80."""
    raw = address - 0x40000
    return bytes([raw & 0xFF, ((raw >> 8) & 0xFF) + 0x80])


def decode_p40000(raw: bytes) -> int:
    return 0x40000 | ((raw[1] - 0x80) << 8) | raw[0]


def encode_p48000(address: int) -> bytes:
    """P48000 pointer: 16-bit offset from 0x40000, unbiased."""
    raw = address - 0x40000
    return bytes([raw & 0xFF, (raw >> 8) & 0xFF])


def decode_p48000(raw: bytes) -> int:
    return 0x40000 + raw[0] + (raw[1] << 8)


def encode_p17000(address: int) -> bytes:
    """P17000 pointer: 16-bit offset from 0x10000 with the high byte biased 0x80."""
    raw = address - 0x10000
    return bytes([raw & 0xFF, ((raw >> 8) & 0xFF) + 0x80])


def decode_p17000(raw: bytes) -> int:
    return 0x10000 + ((raw[1] - 0x80) << 8) + raw[0]


def name_text_blob(team: int, entries: int = NAME_TEXT_ENTRIES) -> bytes:
    """One selection-screen name blob: a count byte then `count` 4-byte entries.

    The entries are filler, not a real display list: `write_team_name_texts`
    reads a blob's *length* out of the count byte and copies the bytes through
    untouched for an unpatched team, so what is in them only has to be
    recognisable.
    """
    body = bytearray()
    for i in range(entries):
        body.extend([0xF9, (team * 7 + i) & 0xFF, 0x40 + i, 0x06])
    return bytes([entries]) + bytes(body)


def konami_literal(raw: bytes) -> bytes:
    """The literal-only Konami stream: 2-byte LE total, then `0x80|n` runs of n.

    Retranscribed from the format `_konami_compress_literal` documents so a test
    can predict a patched blob without calling the compressor it is checking.
    """
    out = bytearray([0, 0])
    pos = 0
    while pos < len(raw):
        chunk = min(31, len(raw) - pos)
        out.append(0x80 | chunk)
        out.extend(raw[pos : pos + chunk])
        pos += chunk
    total = len(out)
    out[0] = total & 0xFF
    out[1] = (total >> 8) & 0xFF
    return bytes(out)


def flag_half_blob(team: int, half: int) -> bytes:
    """Team `team`'s top (0) or bottom (1) flag half: a 6-byte stream -- one
    literal byte, `2 * team + half`, then 95 zeros -- so no two are alike."""
    return bytes([6, 0, 0x81, 2 * team + half, 0xFF, 93])


def flag_half_raw(team: int, half: int) -> bytes:
    """The 96 bytes `flag_half_blob(team, half)` decodes to."""
    return bytes([2 * team + half]) + bytes(95)


def lorom(address: int) -> int:
    """A LoROM SNES address as a headerless file offset."""
    return ((address >> 16) & 0x7F) * 0x8000 + (address & 0x7FFF)


def tile_4bpp(rows: list[list[int]]) -> bytes:
    """8 rows of 8 entries as a 32-byte SNES 4bpp tile: planes 0/1 then 2/3."""
    out = bytearray(32)
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            for bit, base in ((0, 0), (1, 1), (2, 16), (3, 17)):
                if value >> bit & 1:
                    out[base + 2 * y] |= 0x80 >> x
    return bytes(out)


def chest_design(team: int) -> int:
    """Pose 0 of the design `team` (enum order) wears on this fixture."""
    if TEAM_ENUM_ORDER[team] == "Germany":
        return BAND_DESIGN
    if TEAM_ENUM_ORDER[team] in ("Austria", "Argentina"):
        return STRIPES_DESIGN
    return BLANK_DESIGN


def chest_pointer(team: int, pose: int) -> int:
    design = chest_design(team) if team < TOTAL_TEAMS else BLANK_DESIGN
    return design if design == BLANK_DESIGN else design + pose * POSE_STRIDE


def flag_sprite_record(team: int) -> bytes:
    """Team `team`'s two 16x16 sprites: count, then (y, x, tile, attr) twice."""
    tile = 0x20 + 3 * team
    return bytes([2, 0, 8, tile + 1, 0x13, 0, 0, tile, 0x13])


def name_tile_blob(team: int, size: int = NAME_TILE_BLOB_SIZE) -> bytes:
    """One name-tile blob: a 2-byte little-endian length, then `size - 2` bytes.

    Not a well-formed Konami stream. `write_name_tiles` copies an unpatched
    team's blob through by length alone and never decompresses it, so the
    payload only has to be distinguishable per team.
    """
    body = bytes(((team * 5 + i) % 0x60) + 0x10 for i in range(size - 2))
    return bytes([size & 0xFF, (size >> 8) & 0xFF]) + body


def desc_offset(team: int) -> int:
    """File offset of one description block."""
    return DESC_BASE + team * DESC_STRIDE


def desc_snes_pointer(team: int) -> int:
    """The bank-$02 SNES address that names `desc_offset(team)`."""
    return desc_offset(team) - DESC_BANK_BASE + DESC_BANK_SNES_BASE


def desc_text(team: int) -> bytes:
    """The 60 ASCII bytes of one team's original description."""
    letter = chr(ord("a") + team % 26)
    return (
        (f"desc{team:02d}" + letter * (DESC_TEXT_LENGTH - 7))
        .encode("ascii")[:DESC_TEXT_LENGTH]
        .ljust(DESC_TEXT_LENGTH, b"z")
    )


def desc_block(team: int) -> bytes:
    """Header, description text, terminator, padded to `DESC_STRIDE`."""
    block = bytearray(DESC_HEADER)
    block.extend(desc_text(team))
    block.append(DESC_TERMINATOR)
    block.extend(b"\x00" * (DESC_STRIDE - len(block)))
    return bytes(block)


def desc_text_start(team: int) -> int:
    """File offset of the first description byte, past the 0xFD control byte."""
    return desc_offset(team) + len(DESC_HEADER)


def centred_description(name: str, available: int = DESC_TEXT_LENGTH) -> bytes:
    """What `write_team_descriptions` produces for `name`, retranscribed.

    Word-wrapped at 15 characters, each line centred and padded to 15, the whole
    padded with spaces to `available` and then truncated to it.
    """
    lines: list[str] = []
    current = ""
    for word in name.split():
        if current and len(current) + 1 + len(word) > DESC_LINE_WIDTH:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    padded = []
    for line in lines:
        line = line[:DESC_LINE_WIDTH]
        pad = DESC_LINE_WIDTH - len(line)
        padded.append(" " * (pad // 2) + line + " " * (pad - pad // 2))
    text = "".join(padded)
    if len(text) < available:
        text += " " * (available - len(text))
    return text[:available].encode("ascii")


#: One 1 MB LCG run costs about a tenth of a second, and this module builds an
#: image per test. The run is deterministic, so the longest one ever asked for is
#: kept and every shorter request is its prefix.
_FILLER_CACHE = bytearray()


def _filler(size: int) -> bytearray:
    """A deterministic pseudo-random fill, every byte in 0x01-0x7F.

    Below 0x80 by construction, which matters in two places: the two biased
    pointer tables need a high byte at or above 0x80 to be valid, so a table
    this fixture did not write cannot pass `signature_ok` by accident, and the
    description scan looks for 0xFD and 0xFF, neither of which the filler can
    produce.
    """
    global _FILLER_CACHE
    if len(_FILLER_CACHE) < size:
        data = bytearray(size)
        state = 0x1234_5678
        for index in range(size):
            state = (state * 1103515245 + 12345) & 0x7FFF_FFFF
            data[index] = 0x01 + ((state >> 16) % 0x7F)
        _FILLER_CACHE = data
    return bytearray(_FILLER_CACHE[:size])


def build_iss_rom(
    *,
    size: int = ROM_SIZE,
    with_header: bool = False,
    name_text_base: int = NAME_TEXT_BASE,
    name_text_entries: int = NAME_TEXT_ENTRIES,
    name_tile_base: int = NAME_TILE_BASE,
    name_tile_blob_size: int = NAME_TILE_BLOB_SIZE,
    name_tile_declared_size: int | None = None,
    break_name_text_bias: bool = False,
    break_name_tile_bounds: bool = False,
    break_desc_bank: bool = False,
    desc_pointer_one_bank_low: bool = False,
) -> bytearray:
    """Return an image the reader accepts and the writer can fill.

    `size` is the size of the body, before any copier header: `with_header`
    prepends 512 more bytes and shifts every offset, which is the arithmetic
    `_seek` does.

    `name_text_base` moves the selection-screen name blobs. Setting it to
    `MAX_NAME_TEXT_ADDR` or above is what makes `write_team_name_texts`'
    budget non-positive; setting it just below leaves a budget too small for 27
    patched names and drives the truncation loop.

    `name_tile_blob_size` scales the 27 compressed blobs. `write_name_tiles`
    refuses a total above `NAME_TILES_CAPACITY`, which 91 bytes apiece exceeds.
    `name_tile_declared_size` rewrites the *first* blob's length word without
    moving anything, so it disagrees with the bytes behind it.

    The three `break_*` flags each corrupt exactly one of the conditions
    `signature_ok` tests, so a test can pin which one it is answering to.

    `desc_pointer_one_bank_low` is the sharper version of `break_desc_bank`:
    it points slot 7's description at a *well-formed* block planted at 0x8DA0,
    one bank below where the table belongs, so that a writer without the
    bank guard finds its 0xFD, computes a description start of 0x8DB4 and
    writes sixty bytes of team name over the predominant-colour table at
    0x8DB2. `break_desc_bank` alone does not reach that: the filler holds no
    0xFD, so an unguarded write finds no start and gives up quietly.
    """
    body = _filler(size)

    def put(offset: int, data: bytes) -> None:
        body[offset : offset + len(data)] = data

    # -- player names, in TEAM_NAME_ORDER positions
    for team in range(TOTAL_TEAMS):
        for slot in range(PLAYERS_PER_TEAM):
            put(
                OFS_PLAYER_NAMES + (team * PLAYERS_PER_TEAM + slot) * PLAYER_NAME_LENGTH,
                encode_name(player_name(team, slot)),
            )

    # -- player data, in TEAM_ENUM_ORDER positions
    for team in range(TOTAL_TEAMS):
        for slot in range(PLAYERS_PER_TEAM):
            put(
                OFS_PLAYER_DATA + (team * PLAYERS_PER_TEAM + slot) * PLAYER_DATA_LENGTH,
                player_data_record(team, slot),
            )

    # -- the ten machine-code operand bytes, unpatched
    for point in DISPLACEMENT_PATCH_POINTS:
        body[point] = UNPATCHED_BANK_BYTE

    # -- selection-screen name text: pointer table then blobs
    addr = name_text_base
    for team in range(TOTAL_TEAMS):
        blob = name_text_blob(team, name_text_entries)
        put(OFS_TEAM_NAME_TEXT_PTRS + team * 2, encode_p40000(addr))
        put(addr, blob)
        addr += len(blob)
    if break_name_text_bias:
        # One high byte below the 0x80 bias, which `_decode_p40000` turns into a
        # negative file offset.
        body[OFS_TEAM_NAME_TEXT_PTRS + 13 * 2 + 1] = 0x7F

    # -- flags: two halves per team from the start of the flag room, the cells
    # of the preload screens, and the sprite records that draw them
    addr = FLAG_ROOM_START
    for team in range(TOTAL_TEAMS):
        halves = [flag_half_blob(team, half) for half in (0, 1)]
        put(addr, halves[0] + halves[1])
        put(OFS_FLAG_TILE_PTRS + team * 4, encode_p48000(addr) + encode_p48000(addr + 6))
        addr += 12
        borrows = TEAM_ENUM_ORDER[team] in OWN_FLAG_CELLS
        put(
            OFS_FLAG_CELLS + team * 2,
            struct.pack("<H", 0xFFFF if borrows else 0x1000 + 0x30 * team),
        )
        record = FLAG_SPRITE_BASE + team * 9
        put(OFS_FLAG_SPRITES + team * 2, struct.pack("<H", record - 0x40000 + 0x8000))
        put(record, flag_sprite_record(team))

    # -- in-game name tiles: pointer table then blobs
    addr = name_tile_base
    for team in range(TOTAL_TEAMS):
        blob = name_tile_blob(team, name_tile_blob_size)
        put(OFS_NAME_TILES_PTRS + team * 2, encode_p48000(addr))
        put(addr, blob)
        addr += len(blob)
    if break_name_tile_bounds:
        # A blob shorter than the two-byte length word every Konami stream
        # begins with, which no valid stream can be.
        put(name_tile_base, bytes([0x01, 0x00]))
    if name_tile_declared_size is not None:
        # A length word that disagrees with the bytes actually there. On an
        # image near `SIZE_ARITHMETIC_MINIMUM` a large enough one runs off the
        # end, which a 1 MB image cannot reach: a P48000 pointer tops out at
        # 0x4FFFF and a 16-bit length cannot carry it past 0x5FFFE.
        put(name_tile_base, bytes([name_tile_declared_size & 0xFF, name_tile_declared_size >> 8]))

    # -- chest designs: the table, the blank tile and the two designs' poses
    if size > lorom(STRIPES_DESIGN + 7 * POSE_STRIDE) + 34:
        for pose in range(CHEST_POSES):
            for team in range(CHEST_SLOTS):
                at = OFS_CHEST_PTRS + (pose * CHEST_SLOTS + team) * 3
                put(at, chest_pointer(team, pose).to_bytes(3, "little"))
            for design, rows in ((BAND_DESIGN, BAND_TILE), (STRIPES_DESIGN, STRIPES_TILE)):
                put(lorom(design + pose * POSE_STRIDE), b"\x20\x00" + tile_4bpp(rows))
        put(lorom(BLANK_DESIGN), b"\x20\x00" + bytes(32))

    # -- descriptions: pointer table then blocks
    for team in range(TOTAL_TEAMS):
        put(OFS_DESC_PTRS + team * 2, struct.pack("<H", desc_snes_pointer(team)))
        put(desc_offset(team), desc_block(team))
    if break_desc_bank:
        # A pointer below $8000 is not a bank-$02 address at all.
        put(OFS_DESC_PTRS + 7 * 2, struct.pack("<H", 0x4000))
    if desc_pointer_one_bank_low:
        put(OFS_DESC_PTRS + 7 * 2, struct.pack("<H", LOW_BANK_DESC_SNES_POINTER))
        put(LOW_BANK_DESC_OFFSET, desc_block(7))

    if with_header:
        return _filler(SMC_HEADER_SIZE) + body
    return body


def write_iss_rom(path: pathlib.Path, **kwargs: object) -> pathlib.Path:
    """Write a synthetic ROM to `path` and return it."""
    path.write_bytes(bytes(build_iss_rom(**kwargs)))  # type: ignore[arg-type]
    return path
