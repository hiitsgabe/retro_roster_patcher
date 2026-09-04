"""ISS SNES ROM reader -- validates ROM and reads existing team/player data.

Offset constants sourced from:
  https://github.com/rodmguerra/issparser (ISS Studio Java editor)
  https://github.com/EstebanFuentealba/web-iss-studio (web port)

ISS (International Superstar Soccer, 1994) uses a standard SNES .sfc ROM.
Some ROMs include a 512-byte copier header which shifts all offsets.

**On the ROM's size.** Upstream's module docstring said "the expected ROM size is
2MB ... or 2,097,664 with header" while the constant three lines below it named
the 8 Mbit sizes as the USA/EUR release and set the floor to 1 MB. Both cannot be
right and the code follows the constant, so the sentence is deleted rather than
repeated. What this module can state without a real dump is the arithmetic:
`rom_writer.MIN_PATCHABLE_SIZE` is 296 140 bytes, so a 1 MB floor is three and a
half times more than the writer needs and is a guess about which cartridge this
is, not a statement about whether the data fits. `validate_rom` and `signature_ok`
are the guess; `data_fits` is the arithmetic. `patcher.py` applies them to
different entry points and says why.

**There is no version check, here or anywhere.** `rom_writer.write_name_tiles`
overwrites ten bytes of 65816 code at fixed addresses. Nothing below establishes
that the image is the build those addresses belong to; `signature_ok` tests the
*shape* of three pointer tables, which a different revision of the same game
would very likely still satisfy.
"""

from __future__ import annotations

import os

from .models import (
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TOTAL_TEAMS,
    ISSRomInfo,
    ISSTeamSlot,
    name_storage_index,
)
from .rom_writer import MIN_PATCHABLE_SIZE

# -- ROM size constants ------------------------------------------------------
_ROM_SIZE_8MBIT = 1_048_576  # 1 MB (8 Mbit) -- USA/EUR ISS
_ROM_SIZE_8MBIT_HEADER = 1_049_088  # 1 MB + 512
_ROM_SIZE_16MBIT = 2_097_152  # 2 MB (16 Mbit) -- some variants
_ROM_SIZE_16MBIT_HEADER = 2_097_664  # 2 MB + 512
_HEADER_SIZE = 512
_MIN_ROM_SIZE = _ROM_SIZE_8MBIT  # Minimum valid ROM size

# -- Absolute byte offsets (headerless) --------------------------------------
# Player names: 8 bytes per player, teams in TEAM_NAME_ORDER
_OFS_PLAYER_NAMES = 0x3B62C  # 27 teams x 15 players x 8 bytes = 3240 bytes
_PLAYER_NAME_LENGTH = 8

# Player data block: 6 bytes per player, teams in TEAM_ENUM_ORDER
_OFS_PLAYER_DATA = 0x387EC

# -- Pointer tables the signature check dereferences -------------------------
#
# Retranscribed here rather than imported from `rom_writer`, because the check
# below is a statement about the *image* and not about the writer: if one of
# these offsets ever moves, the two copies disagree and
# `tests/games/iss_snes/test_rom_reader.py` says so, where a shared constant
# would move both at once and keep quiet.
_OFS_TEAM_NAME_TEXT_PTRS = 0x39DAE  # 27 x 2 bytes, P40000 (high byte biased 0x80)
_OFS_NAME_TILES_PTRS = 0x93CD  # 27 x 2 bytes, P48000 (raw 16-bit from 0x40000)
_OFS_DESC_PTRS = 0x38000  # 27 x 2 bytes, SNES bank $02 addresses
_MAX_NAME_TEXT_ADDR = 0x44478
_P40000_BASE = 0x40000
_P40000_HIGH_BIAS = 0x80
_DESC_BANK_SNES_BASE = 0x8000
#: Smallest a Konami-compressed blob can be: the 2-byte little-endian length
#: header, and nothing else.
_MIN_COMPRESSED_BLOB = 2

# -- ISS custom character encoding -------------------------------------------


def _build_byte_to_char() -> dict[int, str]:
    """The ISS font's byte-to-character table, built once at import.

    The decode half of `rom_writer.CHAR_TO_BYTE`. Upstream had both halves here
    *and* the encode half in `rom_writer`, each a module-level empty dict filled
    by a separate `_init_encoding()` called from four places between the two
    files. Two tables of the same fact that nothing compared;
    `tests/games/iss_snes/test_rom_reader.py` now compares them.
    """
    table: dict[int, str] = {
        0x00: " ",
        0x54: ".",
        0x53: "-",
        0x56: '"',
        0x5C: "'",
        0x5F: "/",
    }
    for i, c in enumerate("0123456789"):
        table[0x62 + i] = c
    for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        table[0x6C + i] = c
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        table[0x86 + i] = c
    return table


BYTE_TO_CHAR = _build_byte_to_char()


def decode_iss_name(data: bytes) -> str:
    """Decode an 8-byte ISS player name to a string.

    A byte the font table has no entry for contributes nothing at all -- not a
    placeholder -- so an unrecognised byte in the middle of a name closes the
    gap around it rather than showing up. That is upstream's behaviour and it is
    kept: this text is shown to a user choosing a slot, and the alternative
    every other port in this library uses, a literal `?`, would fill an
    unpatched ISS ROM's names with them wherever the font has a glyph this
    table does not name.
    """
    return "".join(BYTE_TO_CHAR.get(b, "") for b in data).strip()


def _le16(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


class ISSRomReader:
    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.header_offset = 0

    def _detect_header(self, size: int) -> bool:
        """Detect if ROM has a 512-byte copier header."""
        if size in (_ROM_SIZE_8MBIT_HEADER, _ROM_SIZE_16MBIT_HEADER):
            return True
        if size in (_ROM_SIZE_8MBIT, _ROM_SIZE_16MBIT):
            return False
        # Heuristic: if size % 1024 == 512, likely has header
        return (size % 1024) == 512

    def _header_offset_for_size(self, size: int) -> int:
        return _HEADER_SIZE if self._detect_header(size) else 0

    def validate_rom(self) -> bool:
        """Check if the file is big enough to be an ISS SNES ROM.

        SIDE EFFECT: on success this sets `self.header_offset`, which every
        offset in this class and in `ISSRomWriter` is measured from. It is
        upstream's, and both entry points in `patcher.py` call this first for
        that reason and not for the return value.

        HEURISTIC, and nothing more. It is a size floor: it reads no byte of the
        file. The image the writer needs is 296 140 bytes and this asks for
        1 048 576, so what the extra 752 436 bytes buy is a guess that a file
        that big is an 8 Mbit SNES cartridge. Registering this patcher with only
        this check would have made `retro-roster analyze` claim every ROM and
        every ISO in a user's library. `signature_ok` is the check that reads
        the image, and `patcher.analyze_rom` requires both.
        """
        if not os.path.exists(self.rom_path):
            return False
        size = os.path.getsize(self.rom_path)
        if size < _MIN_ROM_SIZE:
            return False
        self.header_offset = self._header_offset_for_size(size)
        return True

    def data_fits(self) -> bool:
        """Does every fixed-offset write in `rom_writer` land inside this file?

        ARITHMETIC BOUND, not a heuristic, and `patcher.py` applies it to `patch`
        as well as to `analyze_rom` for that reason: a file that fails this
        provably cannot be patched. `ISSRomWriter` opens its output `r+b` and
        seeks absolutely, and seeking past the end of a file and writing extends
        it, so without this a 4 KB input produced a 297 KB "patched ROM" made of
        one hole and two flag tiles -- which is the same "success with nothing
        patched" lie the WE2002 and Ken Griffey Jr. ports each had a version of.

        Self-contained on purpose: it re-derives the copier-header offset rather
        than reading `self.header_offset`, so it answers the same whether or not
        `validate_rom` has run.
        """
        if not os.path.exists(self.rom_path):
            return False
        size = os.path.getsize(self.rom_path)
        return size - self._header_offset_for_size(size) >= MIN_PATCHABLE_SIZE

    def signature_ok(self) -> bool:
        """Do the three pointer tables this patcher rewrites look like ISS's?

        HEURISTIC, so `patcher.py` gates only `analyze_rom` on it. A false
        positive costs the user a wrong claim on every unrelated image they own;
        a false negative costs auto-detection alone, because `patch --game
        iss-snes` runs without consulting this.

        Every condition is a precondition of a specific line in `rom_writer`,
        derived rather than invented:

          * the team-name-text table is P40000, whose high byte is biased by
            0x80. `_decode_p40000` on a byte below that answers a *negative*
            file offset, and the first thing `write_team_name_texts` does with
            it is `_seek`. The count byte it then reads must describe a blob
            (`1 + count * 4` bytes) that is inside the file, and the lowest
            address in the table must be below the 0x44478 ceiling or the budget
            that method computes is not positive.
          * the name-tile table is P48000, unbiased, so every entry lands in
            0x40000..0x4FFFF by construction and what has to be checked is that
            the 2-byte length header there is inside the file and describes a
            blob that is too. `write_name_tiles` reads exactly that.
          * the description table holds SNES bank-$02 addresses.
            `write_team_descriptions` maps one to `0x10000 + (addr - 0x8000)`,
            which is an address before the bank for anything under $8000 and a
            negative one under $6000.

        27 entries in each of two tables must have a high byte at or above 0x80,
        which a file of random bytes clears with probability 2**-54. That is what
        makes this a signature and not a size test.

        It reads the whole image, once. `analyze` runs it per registered patcher
        against one file, so the cost is a few megabytes of I/O per probe.
        """
        try:
            with open(self.rom_path, "rb") as handle:
                data = handle.read()
        except OSError:
            # `analyze_rom` converts a missing or unreadable file into `RomError`
            # before it gets here; answering False keeps this method total for
            # any other caller.
            return False

        size = len(data)
        base = self._header_offset_for_size(size)
        if size - base < MIN_PATCHABLE_SIZE:
            return False

        # -- team name text: P40000, biased high byte, in-bounds blob
        #
        # `data[base + addr]` below needs no bounds test of its own: the bias
        # check two lines above it puts `addr` in 0x40000..0x47FFF, and the
        # size test above puts `size - base` at 296 140 or more, which is past
        # 0x47FFF.
        name_text_addrs = []
        for i in range(TOTAL_TEAMS):
            low = data[base + _OFS_TEAM_NAME_TEXT_PTRS + i * 2]
            high = data[base + _OFS_TEAM_NAME_TEXT_PTRS + i * 2 + 1]
            if high < _P40000_HIGH_BIAS:
                return False
            addr = _P40000_BASE | ((high - _P40000_HIGH_BIAS) << 8) | low
            blob_end = addr + 1 + data[base + addr] * 4
            if base + blob_end > size:
                return False
            name_text_addrs.append(addr)
        if min(name_text_addrs) >= _MAX_NAME_TEXT_ADDR:
            return False

        # -- name tiles: P48000, unbiased, 2-byte length header
        for i in range(TOTAL_TEAMS):
            addr = _P40000_BASE + _le16(data, base + _OFS_NAME_TILES_PTRS + i * 2)
            if base + addr + _MIN_COMPRESSED_BLOB > size:
                return False
            blob_size = _le16(data, base + addr)
            if blob_size < _MIN_COMPRESSED_BLOB or base + addr + blob_size > size:
                return False

        # -- descriptions: SNES bank $02 addresses
        for i in range(TOTAL_TEAMS):
            if _le16(data, base + _OFS_DESC_PTRS + i * 2) < _DESC_BANK_SNES_BASE:
                return False

        return True

    def read_player_names(self) -> list[list[str]]:
        """Read all player names. Returns list of 27 teams, each with 15 names.

        Names are stored in TEAM_NAME_ORDER, so index this by
        `models.name_storage_index(enum_index)` and not by the enum index.
        """
        names = []
        with open(self.rom_path, "rb") as f:
            base = _OFS_PLAYER_NAMES + self.header_offset
            for team_idx in range(TOTAL_TEAMS):
                team_names = []
                for player_idx in range(PLAYERS_PER_TEAM):
                    offset = base + (team_idx * PLAYERS_PER_TEAM + player_idx) * _PLAYER_NAME_LENGTH
                    f.seek(offset)
                    data = f.read(_PLAYER_NAME_LENGTH)
                    team_names.append(decode_iss_name(data))
                names.append(team_names)
        return names

    def read_team_slots(self) -> list[ISSTeamSlot]:
        """Return the 27 team slots, each with the name of its first player.

        DELIBERATE DIVERGENCE. Upstream answered `ISSTeamSlot(index,
        current_name=TEAM_ENUM_ORDER[i], enum_name=TEAM_ENUM_ORDER[i])` -- the
        same constant twice and not one byte read from the image, so `analyze`
        printed the same 27 lines for every file it was pointed at.
        `RomSlot.current_name` is documented in `core/models.py` as what the ROM
        says today, and this is the one string this reader can honestly put
        there: the ROM's team names are not in `models.py` at all and nothing
        here parses them. The NBA Live 95 and Ken Griffey Jr. ports answer the
        same shape for the same reason.

        The `name_storage_index` call is the reason that function is in
        `models.py` rather than inline in the writer, where upstream had it:
        slot `i`'s players are stored at name-order index, not at `i`, and this
        is the second place that has to know it.
        """
        names = self.read_player_names()
        return [
            ISSTeamSlot(
                index=i,
                name=name,
                first_player=names[name_storage_index(i)][0],
            )
            for i, name in enumerate(TEAM_ENUM_ORDER)
        ]

    def get_rom_info(self) -> ISSRomInfo:
        """Read ROM and return info including available team slots."""
        is_valid = self.validate_rom() and self.data_fits() and self.signature_ok()
        size = os.path.getsize(self.rom_path) if os.path.exists(self.rom_path) else 0
        team_slots = self.read_team_slots() if is_valid else []
        return ISSRomInfo(
            path=self.rom_path,
            size=size,
            team_slots=team_slots,
            is_valid=is_valid,
            has_header=self.header_offset > 0,
        )
