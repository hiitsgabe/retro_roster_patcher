"""ROM reader for NHL94 SNES patcher.

  - https://github.com/clandrew/nhl94e (nhl94e editor source)
  - https://cml-a.com/content/2020/11/23/names-and-stats-in-nhl-94/
  - https://forum.nhl94.com/index.php?/topic/13150-snes-nhl94-rom-mapping-project/

ROM layout (LoROM, 8 Mbit = 1 048 576 bytes):
  - Pointer table at ROM $9CA5E7 -> file offset 0xE25E7 (headerless)
  - 28 teams, 4 bytes per pointer (only low 2 bytes used; bank $9C hardcoded)
  - Each team: [2-byte header size][header...][player records...][terminator][strings]
  - Player record: [2-byte LE name length (includes self)][name bytes][8 stat bytes]

The length prefix is LITTLE-endian here; the Genesis build stores the same
structure big-endian.
"""

import os

from .models import (
    NHL94_TEAM_ORDER,
    TEAM_COUNT,
    NHL94RomInfo,
    NHL94TeamSlot,
)

# Pointer table in a headerless dump
POINTER_TABLE_FILE_OFFSET = 0xE25E7

# 4 bytes per entry, only the low 2 used; bank $9C is hardcoded by the game
POINTER_SIZE = 4
BANK = 0x9C

SMC_HEADER_SIZE = 512

# Upstream's sizes, known wrong, preserved for byte fidelity. The real 8 Mbit
# LoROM is 1 048 576 headerless and 1 049 088 headered; these are 397 KB short, so
# `validate` accepts a file with no bank $9C at all. `patcher._pointer_table_fits`
# rejects it instead. Do not "fix" the numbers here.
ROM_SIZE_NO_HEADER = 649728  # 0x9EC00
ROM_SIZE_WITH_HEADER = 650240  # 0x9EE00

# jersey + 7 attribute bytes
STATS_SIZE = 8

# Team-data byte 17: high nibble forwards, low nibble defencemen.
# Goalies are always 2 and are not encoded.
PLAYER_COUNT_OFFSET = 17


def snes_to_file_offset(rom_addr: int) -> int:
    """Convert a SNES LoROM address to a file offset (headerless)."""
    section = (rom_addr - 0x800000) >> 16
    offset_within_section = rom_addr % 0x8000
    return section * 0x8000 + offset_within_section


class NHL94SNESRomReader:
    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.data: bytearray | None = None
        self.has_header: bool = False
        self.header_offset: int = 0

    def load(self) -> bool:
        if not os.path.exists(self.rom_path):
            return False

        try:
            with open(self.rom_path, "rb") as f:
                self.data = bytearray(f.read())

            self._detect_header()
            return True
        except Exception:
            return False

    def _detect_header(self) -> None:
        """A headered dump has size % 0x8000 == 512."""
        if not self.data:
            return
        if len(self.data) % 0x8000 == SMC_HEADER_SIZE:
            self.has_header = True
            self.header_offset = SMC_HEADER_SIZE
        else:
            self.has_header = False
            self.header_offset = 0

    def validate(self) -> bool:
        if not self.data:
            return False

        size = len(self.data)
        if size == ROM_SIZE_NO_HEADER or size == ROM_SIZE_WITH_HEADER:
            return True
        # nhl94e expands the image to 4 MB
        stripped = size - self.header_offset
        if stripped >= ROM_SIZE_NO_HEADER:
            return True
        return False

    def get_info(self) -> NHL94RomInfo:
        if not self.data:
            return NHL94RomInfo(
                path=self.rom_path,
                size=0,
                team_slots=[],
                is_valid=False,
                has_header=False,
            )

        is_valid = self.validate()
        team_slots = self._read_team_slots() if is_valid else []

        return NHL94RomInfo(
            path=self.rom_path,
            size=len(self.data),
            team_slots=team_slots,
            is_valid=is_valid,
            has_header=self.has_header,
        )

    def _ptr_table_offset(self) -> int:
        return self.header_offset + POINTER_TABLE_FILE_OFFSET

    def _read_team_pointer(self, team_index: int) -> int | None:
        if not self.data or team_index >= TEAM_COUNT:
            return None

        table_off = self._ptr_table_offset()
        ptr_off = table_off + (team_index * POINTER_SIZE)

        if ptr_off + 2 > len(self.data):
            return None

        low = self.data[ptr_off]
        high = self.data[ptr_off + 1]
        rom_addr = (BANK << 16) | (high << 8) | low

        return self.header_offset + snes_to_file_offset(rom_addr)

    def read_team_player_counts(self, team_index: int) -> tuple[int, int, int]:
        """(goalies, forwards, defencemen) from team-data byte 17."""
        if not self.data or team_index >= TEAM_COUNT:
            return (2, 14, 7)

        file_off = self._read_team_pointer(team_index)
        if file_off is None:
            return (2, 14, 7)

        count_off = file_off + PLAYER_COUNT_OFFSET
        if count_off >= len(self.data):
            return (2, 14, 7)

        count_byte = self.data[count_off]
        num_forwards = (count_byte >> 4) & 0x0F
        num_defensemen = count_byte & 0x0F

        if num_forwards < 3 or num_defensemen < 2:
            return (2, 14, 7)

        return (2, num_forwards, num_defensemen)

    def _read_team_slots(self) -> list[NHL94TeamSlot]:
        slots: list[NHL94TeamSlot] = []
        if not self.data:
            return slots

        for i in range(TEAM_COUNT):
            file_off = self._read_team_pointer(i)
            name = ""
            if file_off is not None and file_off < len(self.data):
                name = self._read_team_city(file_off)

            slots.append(
                NHL94TeamSlot(
                    index=i,
                    current_name=name or NHL94_TEAM_ORDER[i],
                    display_name=(
                        NHL94_TEAM_ORDER[i] if i < len(NHL94_TEAM_ORDER) else f"Team {i}"
                    ),
                )
            )

        return slots

    def _read_length_prefixed_string(self, offset: int) -> tuple[str, int]:
        """Read a 2-byte LE length-prefixed string. The length includes itself."""
        assert self.data is not None
        if offset + 2 > len(self.data):
            return "", 0

        length = self.data[offset] | (self.data[offset + 1] << 8)
        if length < 2 or length > 40:
            return "", 0

        str_len = length - 2
        str_start = offset + 2
        if str_start + str_len > len(self.data):
            return "", 0

        try:
            name = (
                bytes(self.data[str_start : str_start + str_len])
                .decode("ascii", errors="replace")
                .strip("\x00")
            )
            return name, length
        except Exception:
            return "", 0

    def _skip_team_header(self, team_data_offset: int) -> int:
        """The first 2 bytes of team data are the header size (LE); the player
        records start just past it."""
        assert self.data is not None
        if team_data_offset + 2 > len(self.data):
            return team_data_offset

        header_size = self.data[team_data_offset] | (self.data[team_data_offset + 1] << 8)
        return team_data_offset + header_size

    def _read_team_city(self, team_data_offset: int) -> str:
        """The city is the first string after the player records."""
        assert self.data is not None
        offset = self._skip_team_header(team_data_offset)

        while offset < len(self.data) - 1:
            length = self.data[offset] | (self.data[offset + 1] << 8)
            if length < 3:  # terminator, 0x0200 or 0x0000
                offset += 2
                break
            offset += length + STATS_SIZE

        city, _ = self._read_length_prefixed_string(offset)
        return city

    def read_team_roster(self, team_index: int) -> tuple[list[str], list[bytes]]:
        if not self.data or team_index >= TEAM_COUNT:
            return [], []

        file_off = self._read_team_pointer(team_index)
        if file_off is None:
            return [], []

        offset = self._skip_team_header(file_off)

        names: list[str] = []
        stat_bytes: list[bytes] = []

        while offset < len(self.data) - 1:
            length = self.data[offset] | (self.data[offset + 1] << 8)
            if length < 3:  # terminator
                break

            str_len = length - 2
            str_start = offset + 2

            if str_start + str_len > len(self.data):
                break

            try:
                name = (
                    bytes(self.data[str_start : str_start + str_len])
                    .decode("ascii", errors="replace")
                    .strip("\x00")
                )
                names.append(name)
            except Exception:
                names.append("")

            offset += length  # length includes the 2 length bytes

            if offset + STATS_SIZE > len(self.data):
                break

            stat_bytes.append(bytes(self.data[offset : offset + STATS_SIZE]))
            offset += STATS_SIZE

        return names, stat_bytes
