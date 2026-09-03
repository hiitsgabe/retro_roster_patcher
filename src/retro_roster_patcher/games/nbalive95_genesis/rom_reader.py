"""ROM reader for NBA Live 95 patcher.

Reads NBA Live 95 (Sega Genesis) ROM data.
Big-endian format, ~2MB ROM.

References:
  - https://github.com/Team-95/rom-edit
"""

import os
import struct

from .models import (
    BYTE_TO_POSITION,
    NAME_LENGTH,
    NBALIVE95_TEAM_ORDER,
    OFF_EXPERIENCE,
    OFF_HAIR,
    OFF_HEIGHT,
    OFF_JERSEY,
    OFF_NAME,
    OFF_POSITION,
    OFF_RATINGS,
    OFF_SKIN,
    OFF_STATS,
    OFF_WEIGHT,
    PLAYER_SIZE,
    PLAYERS_PER_TEAM,
    RATING_COUNT,
    STAT_COUNT,
    TEAM_COUNT,
    TEAM_POINTER_SIZE,
    TEAM_ROSTER_ADDRESSES,
    NBALive95RomInfo,
    NBALive95TeamSlot,
)

# Expected ROM size (~2MB Genesis ROM).
#
# DEFECT, carried over verbatim. `ROM_SIZE_MIN` is 1 572 864 bytes, but the last
# entry of `TEAM_ROSTER_ADDRESSES` is 0x1F80AC and its twelfth pointer ends at
# 0x1F80DC = 2 064 604 -- 491 740 bytes past that minimum. NBA Live 95 is a 2 MB
# (0x200000) cartridge, so a real dump is fine; the consequence is that every
# file between 1.5 MB and ~2.0 MB passes `validate`, because `validate` only
# dereferences team 0 at 0x3FEB4, and then teams 18-29 read their pointers past
# the end of the image. `_get_player_offset` answers 0 for each, every write for
# those teams is skipped, and upstream still reported success. The constant is
# left alone -- the rest of this module is a faithful copy and moving one number
# here silently changes which files `validate` accepts -- and `patcher.py`
# refuses such a file in `_pointer_tables_fit` instead.
ROM_SIZE_MIN = 0x180000  # 1.5 MB minimum
ROM_SIZE_MAX = 0x300000  # 3 MB maximum


class NBALive95RomReader:
    """Reads and parses NBA Live 95 Genesis ROM data."""

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
        """Validate that this is an NBA Live 95 ROM."""
        if not self.data:
            return False
        size = len(self.data)
        if size < ROM_SIZE_MIN or size > ROM_SIZE_MAX:
            return False

        # Check Genesis header for game title (domestic name at 0x120, 48 bytes)
        if size > 0x180:
            title = self.data[0x120:0x150].decode("ascii", errors="replace").strip()
            # Must contain "95" — reject NBA Live 96/97/98 etc.
            if "NBA" in title.upper() and "95" not in title:
                return False

        # Check that team 0 roster address is within ROM
        team0_addr = TEAM_ROSTER_ADDRESSES[0]
        if team0_addr + TEAM_POINTER_SIZE * PLAYERS_PER_TEAM > size:
            return False

        # Read first player pointer for team 0 and verify it points within ROM
        first_ptr = struct.unpack_from(">I", self.data, team0_addr)[0]
        if first_ptr == 0 or first_ptr + PLAYER_SIZE > size:
            return False

        # Verify the pointer leads to plausible player data
        # Check that the player name area contains ASCII
        name_off = first_ptr + OFF_NAME
        if name_off + NAME_LENGTH > size:
            return False

        name_bytes = self.data[name_off : name_off + NAME_LENGTH]
        ascii_count = sum(1 for b in name_bytes if 0x20 <= b <= 0x7E)
        if ascii_count < 3:
            return False

        return True

    def get_info(self) -> NBALive95RomInfo:
        """Get ROM information and team slots."""
        if not self.data:
            return NBALive95RomInfo(path=self.rom_path, size=0)
        is_valid = self.validate()
        team_slots = self._read_team_slots() if is_valid else []
        return NBALive95RomInfo(
            path=self.rom_path,
            size=len(self.data),
            team_slots=team_slots,
            is_valid=is_valid,
        )

    def _get_team_roster_offset(self, team_index: int) -> int:
        """Get offset of a team's roster pointer table.

        Team roster addresses are hardcoded (not evenly spaced) —
        there's a large gap between teams 17 and 18 in the ROM.
        """
        if team_index < 0 or team_index >= len(TEAM_ROSTER_ADDRESSES):
            return 0
        return TEAM_ROSTER_ADDRESSES[team_index]

    def _get_player_offset(self, team_index: int, player_slot: int) -> int:
        """Get absolute offset of a player record by following the pointer."""
        if not self.data:
            return 0
        roster_off = self._get_team_roster_offset(team_index)
        ptr_off = roster_off + player_slot * TEAM_POINTER_SIZE

        if ptr_off + TEAM_POINTER_SIZE > len(self.data):
            return 0

        # Read 4-byte big-endian pointer
        player_ptr = struct.unpack_from(">I", self.data, ptr_off)[0]

        if player_ptr == 0 or player_ptr + PLAYER_SIZE > len(self.data):
            return 0

        return player_ptr

    def _decode_name(self, data_bytes: bytes | bytearray) -> tuple[str, str]:
        """Decode player name from 24-byte ASCII field.

        Format: "LASTNAME\\0FIRST" or "LASTNAME\\0F."
        Returns (last_name, first_name).
        """
        # Find null separator
        null_pos = -1
        for i, b in enumerate(data_bytes):
            if b == 0x00:
                null_pos = i
                break

        if null_pos < 0:
            # No null found — treat entire field as last name
            name = (
                bytes(b for b in data_bytes if 0x20 <= b <= 0x7E)
                .decode("ascii", errors="replace")
                .strip()
            )
            return name, ""

        last_bytes = data_bytes[:null_pos]
        first_bytes = data_bytes[null_pos + 1 :]

        # First name also ends at a null byte
        first_null = -1
        for i, b in enumerate(first_bytes):
            if b == 0x00:
                first_null = i
                break
        if first_null >= 0:
            first_bytes = first_bytes[:first_null]

        last = (
            bytes(b for b in last_bytes if 0x20 <= b <= 0x7E)
            .decode("ascii", errors="replace")
            .strip()
        )
        first = (
            bytes(b for b in first_bytes if 0x20 <= b <= 0x7E)
            .decode("ascii", errors="replace")
            .strip()
        )

        return last, first

    def read_player(self, team_index: int, player_slot: int) -> dict:
        """Read a single player record from ROM.

        Returns dict with all parsed fields.
        """
        if not self.data:
            return {}
        off = self._get_player_offset(team_index, player_slot)
        if off == 0 or off + PLAYER_SIZE > len(self.data):
            return {}

        d = self.data

        last_name, first_name = self._decode_name(d[off + OFF_NAME : off + OFF_NAME + NAME_LENGTH])

        position_byte = d[off + OFF_POSITION]
        position = BYTE_TO_POSITION.get(position_byte, f"?{position_byte}")

        # Read 16 ratings
        ratings = list(d[off + OFF_RATINGS : off + OFF_RATINGS + RATING_COUNT])

        # Read 16 season stats (2-byte BE each)
        stats = []
        for i in range(STAT_COUNT):
            stat_off = off + OFF_STATS + i * 2
            val = struct.unpack_from(">H", d, stat_off)[0]
            stats.append(val)

        return {
            "last_name": last_name,
            "first_name": first_name,
            "jersey": d[off + OFF_JERSEY],
            "position": position,
            "position_byte": position_byte,
            "height_inches": d[off + OFF_HEIGHT] + 5,
            "weight_lbs": d[off + OFF_WEIGHT] + 100,
            "experience": d[off + OFF_EXPERIENCE],
            "skin_color": d[off + OFF_SKIN],
            "hair_style": d[off + OFF_HAIR],
            "ratings": ratings,
            "season_stats": stats,
            "offset": off,
        }

    def read_team_roster(self, team_index: int) -> list[dict]:
        """Read all players for a team.

        Returns list of player dicts.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return []

        players = []
        for slot in range(PLAYERS_PER_TEAM):
            p = self.read_player(team_index, slot)
            if not p:
                break
            players.append(p)

        return players

    def _read_team_slots(self) -> list[NBALive95TeamSlot]:
        """Read team slots for ROM info display."""
        slots = []
        for i in range(TEAM_COUNT):
            first_player = ""
            p = self.read_player(i, 0)
            if p:
                first = p.get("first_name", "")
                last = p.get("last_name", "")
                if first and last:
                    first_player = f"{first} {last}"
                elif last:
                    first_player = last
            slots.append(
                NBALive95TeamSlot(
                    index=i,
                    name=(
                        NBALIVE95_TEAM_ORDER[i] if i < len(NBALIVE95_TEAM_ORDER) else f"Team {i}"
                    ),
                    first_player=first_player,
                )
            )
        return slots
