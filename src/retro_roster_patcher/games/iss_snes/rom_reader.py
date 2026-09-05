"""ISS SNES ROM reader -- validates ROM and reads existing team/player data.

Offset constants sourced from:
  https://github.com/rodmguerra/issparser (ISS Studio Java editor)
  https://github.com/EstebanFuentealba/web-iss-studio (web port)

ISS (International Superstar Soccer, 1994) uses a standard SNES .sfc ROM.
Some ROMs include a 512-byte copier header which shifts all offsets.

`validate_rom`'s 1 MB floor and `signature_ok` are guesses about which cartridge
this is; `data_fits` is arithmetic against `rom_writer.MIN_PATCHABLE_SIZE`
(296 140 bytes). Keep them apart: only the arithmetic may refuse a patch.

There is no version check, here or anywhere. `rom_writer.write_name_tiles`
overwrites ten bytes of 65816 code at fixed addresses, and `signature_ok` tests
only the *shape* of three pointer tables, which a different revision of the same
game would very likely still satisfy.
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

_ROM_SIZE_8MBIT = 1_048_576  # 1 MB (8 Mbit) -- USA/EUR ISS
_ROM_SIZE_8MBIT_HEADER = 1_049_088  # 1 MB + 512
_ROM_SIZE_16MBIT = 2_097_152  # 2 MB (16 Mbit) -- some variants
_ROM_SIZE_16MBIT_HEADER = 2_097_664  # 2 MB + 512
_HEADER_SIZE = 512
_MIN_ROM_SIZE = _ROM_SIZE_8MBIT

# Offsets below are absolute and headerless.

# Player names: 8 bytes per player, teams in TEAM_NAME_ORDER
_OFS_PLAYER_NAMES = 0x3B62C  # 27 teams x 15 players x 8 bytes = 3240 bytes
_PLAYER_NAME_LENGTH = 8

# Player data block: 6 bytes per player, teams in TEAM_ENUM_ORDER
_OFS_PLAYER_DATA = 0x387EC

# Pointer tables the signature check dereferences. Retranscribed, never imported
# from `rom_writer`: they state what the *image* must be, so a drift between the
# two copies is a real finding rather than a silent shared edit.
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


def _build_byte_to_char() -> dict[int, str]:
    """The decode half of `rom_writer.CHAR_TO_BYTE`.

    Build it once and return it; never fill a module-level dict by side effect.
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
    `?` placeholder -- so an unrecognised byte closes the gap around it. Keep it
    that way: the ISS font has glyphs this table does not name, and an unpatched
    ROM's names would otherwise come back full of placeholders.
    """
    return "".join(BYTE_TO_CHAR.get(b, "") for b in data).strip()


def _le16(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


class ISSRomReader:
    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.header_offset = 0

    def _detect_header(self, size: int) -> bool:
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
        offset in this class and in `ISSRomWriter` is measured from. Both entry
        points in `patcher.py` call it for that, not for the return value.

        HEURISTIC: a 1 MB size floor that reads no byte of the file, where the
        writer only needs 296 140. `signature_ok` is the check that reads the
        image, and `patcher.analyze_rom` requires both.
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

        ARITHMETIC BOUND, so `patcher.py` applies it to `patch` as well as to
        `analyze_rom`: `ISSRomWriter` opens its output `r+b` and seeks
        absolutely, and seeking past the end and writing extends the file, so
        without this a 4 KB input yields a 297 KB "patched ROM" of one hole and
        two flag tiles.

        Re-derive the header offset here rather than reading
        `self.header_offset`, so the answer does not depend on `validate_rom`.
        """
        if not os.path.exists(self.rom_path):
            return False
        size = os.path.getsize(self.rom_path)
        return size - self._header_offset_for_size(size) >= MIN_PATCHABLE_SIZE

    def signature_ok(self) -> bool:
        """Do the three pointer tables this patcher rewrites look like ISS's?

        HEURISTIC, so `patcher.py` gates only `analyze_rom` on it. Every
        condition is a precondition of a specific line in `rom_writer`:

          * the team-name-text table is P40000, whose high byte is biased by
            0x80; below that, `_decode_p40000` answers a *negative* file offset.
            The count byte it points at must describe a `1 + count * 4` blob
            inside the file, and the lowest address in the table must be below
            the 0x44478 ceiling or `write_team_name_texts`' budget is not
            positive.
          * the name-tile table is P48000, unbiased, so every entry lands in
            0x40000..0x4FFFF by construction; what is checked is that its 2-byte
            length header, and the blob that header describes, are in the file.
          * the description table holds SNES bank-$02 addresses.
            `write_team_descriptions` maps one to `0x10000 + (addr - 0x8000)`,
            which reduces to `0x8000 + addr`, so anything under $8000 lands one
            bank low, in the range holding this game's predominant-colour byte
            table and two of its pointer tables.

        27 entries in each of two tables must have a high byte at or above 0x80,
        which a file of random bytes clears with probability 2**-54.

        Do not catch `OSError` here: `Patcher.analyze_rom` promises `RomError`
        for an unreadable file and `is_valid=False` for a different game, and
        swallowing it collapses the two.
        """
        with open(self.rom_path, "rb") as handle:
            data = handle.read()

        size = len(data)
        base = self._header_offset_for_size(size)
        if size - base < MIN_PATCHABLE_SIZE:
            return False

        # Team name text: P40000, biased high byte, in-bounds blob. `data[base +
        # addr]` needs no bounds test: the bias check puts `addr` in
        # 0x40000..0x47FFF and `size - base` is at least 296 140.
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

        # Name tiles: P48000, unbiased, 2-byte length header.
        for i in range(TOTAL_TEAMS):
            addr = _P40000_BASE + _le16(data, base + _OFS_NAME_TILES_PTRS + i * 2)
            if base + addr + _MIN_COMPRESSED_BLOB > size:
                return False
            blob_size = _le16(data, base + addr)
            if blob_size < _MIN_COMPRESSED_BLOB or base + addr + blob_size > size:
                return False

        # Descriptions: SNES bank $02 addresses.
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

        The player name must stay: it is the only ROM-derived string this reader
        can put in `RomSlot.current_name`, and returning the `TEAM_ENUM_ORDER`
        constant instead would make `analyze` print the same 27 lines for every
        file. Slot `i`'s players sit at `name_storage_index(i)`, not at `i`.
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
