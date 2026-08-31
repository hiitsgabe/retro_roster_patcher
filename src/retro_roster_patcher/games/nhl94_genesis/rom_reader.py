"""ROM reader for NHL94 Genesis patcher.

Reads NHL94 Sega Genesis ROM (.bin) data to understand team/player structure.

References:
  - https://forum.nhl94.com/index.php?/topic/26353-how-to-manually-edit-the-team-player-data-nhl-94/
  - https://nhl94.com/html/editing/edit_bin.php

ROM layout (Motorola 68000, big-endian):
  - Pointer table at file offset 0x030E, 4 bytes per team (absolute ROM addresses)
  - 26 teams in the original ROM
  - Each team data block starts with 6 two-byte index pointers (12 bytes)
  - Sub-sections: palettes (64B), team ratings (3B), player counts,
    goalie counts, lines (64B), player records (variable), team strings

  Per-team index at team_base:
    +0x00: 2B → offset to player records (relative to team_base)
    +0x02: 2B → offset to color palettes
    +0x04: 2B → offset to team name strings
    +0x06: 2B → offset to lines data
    +0x08: 2B → offset to team ratings / player counts
    +0x0A: 2B → offset to goalie counts

  Player record format (variable length):
    [2 bytes] name length (big-endian), includes the 2-byte length field
    [N bytes] player name (ASCII)
    [1 byte]  jersey number (BCD)
    [7 bytes] 14 attribute nibbles

  End-of-roster sentinel: 0x0000 followed by 0x0002
"""

import os

from .models import (
    NHL94_GEN_TEAM_ORDER,
    TEAM_COUNT,
    NHL94GenRomInfo,
    NHL94GenTeamSlot,
)

# File offset of the team pointer table
POINTER_TABLE_OFFSET = 0x030E

# Each pointer entry is 4 bytes (absolute 68000 ROM address)
POINTER_SIZE = 4

# Stats bytes per player: 1 byte jersey (BCD) + 7 bytes attributes
STATS_SIZE = 8

# Expected ROM size for original NHL94 Genesis (1 MB)
ROM_SIZE_STANDARD = 1048576  # 0x100000

# Checksum bypass: the game's checksum verification routine starts with
# CMP.W #imm,D0 (opcode B0 FC) at this even address. Writing 4E 75 (RTS)
# here makes it return immediately, allowing edited ROMs to boot.
# Must be word-aligned (even) for valid 68000 instruction.
CHECKSUM_BYPASS_OFFSET = 0x0FFACA


class NHL94GenesisRomReader:
    """Reads and parses NHL94 Genesis ROM data."""

    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.data: bytearray | None = None

    def load(self) -> bool:
        """Load ROM file into memory."""
        if not os.path.exists(self.rom_path):
            return False
        try:
            with open(self.rom_path, "rb") as f:
                self.data = bytearray(f.read())
            return True
        except Exception:
            return False

    def validate(self) -> bool:
        """Validate that this is an NHL94 Genesis ROM."""
        if not self.data:
            return False
        size = len(self.data)
        # Accept standard 1 MB ROM and expanded ROMs (up to 4 MB)
        if size < ROM_SIZE_STANDARD:
            return False
        # Verify pointer table has reasonable values
        first_ptr = self._read_team_pointer(0)
        if first_ptr is None or first_ptr >= size:
            return False
        return True

    def get_info(self) -> NHL94GenRomInfo:
        """Get ROM information and team slots."""
        if not self.data:
            return NHL94GenRomInfo(
                path=self.rom_path,
                size=0,
                team_slots=[],
                is_valid=False,
            )
        is_valid = self.validate()
        team_slots = self._read_team_slots() if is_valid else []
        return NHL94GenRomInfo(
            path=self.rom_path,
            size=len(self.data),
            team_slots=team_slots,
            is_valid=is_valid,
        )

    def _read_u16_be(self, offset: int) -> int:
        """Read a big-endian 16-bit unsigned integer."""
        assert self.data is not None
        return (self.data[offset] << 8) | self.data[offset + 1]

    def _read_u32_be(self, offset: int) -> int:
        """Read a big-endian 32-bit unsigned integer."""
        assert self.data is not None
        return (
            (self.data[offset] << 24)
            | (self.data[offset + 1] << 16)
            | (self.data[offset + 2] << 8)
            | self.data[offset + 3]
        )

    def _read_team_pointer(self, team_index: int) -> int | None:
        """Read team data file offset from pointer table.

        The pointer table stores absolute 68000 addresses.
        For Genesis, file offset == ROM address (no banking).
        """
        if not self.data or team_index >= TEAM_COUNT:
            return None
        ptr_off = POINTER_TABLE_OFFSET + (team_index * POINTER_SIZE)
        if ptr_off + 4 > len(self.data):
            return None
        addr = self._read_u32_be(ptr_off)
        if addr >= len(self.data):
            return None
        return addr

    def _read_team_slots(self) -> list[NHL94GenTeamSlot]:
        """Read team information from ROM."""
        slots: list[NHL94GenTeamSlot] = []
        if not self.data:
            return slots
        for i in range(TEAM_COUNT):
            name = ""
            team_base = self._read_team_pointer(i)
            if team_base is not None:
                name = self._read_team_city(team_base)
            display = NHL94_GEN_TEAM_ORDER[i] if i < len(NHL94_GEN_TEAM_ORDER) else f"Team {i}"
            slots.append(
                NHL94GenTeamSlot(
                    index=i,
                    current_name=name or display,
                    display_name=display,
                )
            )
        return slots

    def get_team_section_offsets(self, team_index: int) -> dict[str, int] | None:
        """Get absolute offsets of all team data sections.

        Returns dict with keys: players, palettes, strings, lines,
        ratings, goalies. All values are absolute file offsets.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return None
        team_base = self._read_team_pointer(team_index)
        if team_base is None:
            return None
        return {
            "players": team_base + self._read_u16_be(team_base),
            "palettes": team_base + self._read_u16_be(team_base + 2),
            "strings": team_base + self._read_u16_be(team_base + 4),
            "lines": team_base + self._read_u16_be(team_base + 6),
            "ratings": team_base + self._read_u16_be(team_base + 8),
            "goalies": team_base + self._read_u16_be(team_base + 0xA),
        }

    def _get_player_records_offset(self, team_base: int) -> int:
        """Get absolute offset of player records for a team.

        First 2-byte index entry at team_base points to player records
        (relative to team_base).
        """
        rel = self._read_u16_be(team_base)
        return team_base + rel

    def _get_team_strings_offset(self, team_base: int) -> int:
        """Get absolute offset of team name strings.

        Third 2-byte index entry (offset +4) points to team strings.
        """
        rel = self._read_u16_be(team_base + 4)
        return team_base + rel

    def _read_length_prefixed_string(self, offset: int) -> tuple[str, int]:
        """Read a 2-byte BE length-prefixed string.

        The length value includes the 2 length bytes themselves.
        Returns (string, total_bytes_consumed).
        """
        assert self.data is not None
        if offset + 2 > len(self.data):
            return "", 0
        length = self._read_u16_be(offset)
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

    def _read_team_city(self, team_base: int) -> str:
        """Read team city name (first string in the team strings section)."""
        strings_off = self._get_team_strings_offset(team_base)
        city, _ = self._read_length_prefixed_string(strings_off)
        return city

    def read_team_roster(self, team_index: int) -> tuple[list[str], list[bytes]]:
        """Read player names and stat bytes for a team.

        Returns: (names, stat_bytes_list)
        Each stat_bytes entry is 8 bytes: 1 jersey BCD + 7 attribute bytes.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return [], []
        team_base = self._read_team_pointer(team_index)
        if team_base is None:
            return [], []

        offset = self._get_player_records_offset(team_base)
        names = []
        stat_bytes = []

        while offset < len(self.data) - 1:
            length = self._read_u16_be(offset)
            # End-of-roster: length 0 or very small (sentinel 0x0000)
            if length < 3:
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
            # Read 8 stat bytes (jersey BCD + 7 attribute bytes)
            if offset + STATS_SIZE > len(self.data):
                break
            stat_bytes.append(bytes(self.data[offset : offset + STATS_SIZE]))
            offset += STATS_SIZE

        return names, stat_bytes

    def get_team_player_region(self, team_index: int) -> tuple[int, int]:
        """Get file offset and total byte size of a team's player region.

        Returns (start_offset, total_bytes) where start includes all
        player records up to and including the 2-byte end sentinel.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return 0, 0
        team_base = self._read_team_pointer(team_index)
        if team_base is None:
            return 0, 0

        start = self._get_player_records_offset(team_base)
        offset = start

        while offset < len(self.data) - 1:
            length = self._read_u16_be(offset)
            if length < 3:  # Sentinel
                offset += 2  # Include sentinel
                break
            offset += length + STATS_SIZE

        return start, offset - start
