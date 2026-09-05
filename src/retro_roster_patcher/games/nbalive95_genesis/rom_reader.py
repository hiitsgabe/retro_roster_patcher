"""ROM reader for NBA Live 95 patcher. Big-endian, a ~2 MB cartridge.

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

# Upstream's minimum, known wrong, preserved for byte fidelity: the last team
# table ends at 0x1F80DC, 491 740 bytes past it, so a file between 1.5 MB and
# ~2.0 MB validates and then reads teams 18-29 off the end.
# `patcher._pointer_tables_fit` rejects it instead. Do not "fix" the number.
ROM_SIZE_MIN = 0x180000  # 1.5 MB minimum
ROM_SIZE_MAX = 0x300000  # 3 MB maximum


class NBALive95RomReader:
    def __init__(self, rom_path: str):
        self.rom_path = rom_path
        self.data: bytearray | None = None

    def load(self) -> bool:
        if not os.path.exists(self.rom_path):
            return False
        try:
            with open(self.rom_path, "rb") as f:
                self.data = bytearray(f.read())
            return True
        except Exception:
            return False

    def validate(self) -> bool:
        if not self.data:
            return False
        size = len(self.data)
        if size < ROM_SIZE_MIN or size > ROM_SIZE_MAX:
            return False

        # Genesis domestic title, 48 bytes at 0x120. Must say "95", or this is
        # NBA Live 96/97/98.
        if size > 0x180:
            title = self.data[0x120:0x150].decode("ascii", errors="replace").strip()
            if "NBA" in title.upper() and "95" not in title:
                return False

        team0_addr = TEAM_ROSTER_ADDRESSES[0]
        if team0_addr + TEAM_POINTER_SIZE * PLAYERS_PER_TEAM > size:
            return False

        first_ptr = struct.unpack_from(">I", self.data, team0_addr)[0]
        if first_ptr == 0 or first_ptr + PLAYER_SIZE > size:
            return False

        # a plausible record has ASCII in its name field
        name_off = first_ptr + OFF_NAME
        if name_off + NAME_LENGTH > size:
            return False

        name_bytes = self.data[name_off : name_off + NAME_LENGTH]
        ascii_count = sum(1 for b in name_bytes if 0x20 <= b <= 0x7E)
        if ascii_count < 3:
            return False

        return True

    def get_info(self) -> NBALive95RomInfo:
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
        """The roster addresses are hardcoded and not evenly spaced: there is a
        large gap between teams 17 and 18."""
        if team_index < 0 or team_index >= len(TEAM_ROSTER_ADDRESSES):
            return 0
        return TEAM_ROSTER_ADDRESSES[team_index]

    def _get_player_offset(self, team_index: int, player_slot: int) -> int:
        if not self.data:
            return 0
        roster_off = self._get_team_roster_offset(team_index)
        ptr_off = roster_off + player_slot * TEAM_POINTER_SIZE

        if ptr_off + TEAM_POINTER_SIZE > len(self.data):
            return 0

        player_ptr = struct.unpack_from(">I", self.data, ptr_off)[0]

        if player_ptr == 0 or player_ptr + PLAYER_SIZE > len(self.data):
            return 0

        return player_ptr

    def _decode_name(self, data_bytes: bytes | bytearray) -> tuple[str, str]:
        """The 24-byte ASCII name field is "LASTNAME\\0FIRST" or "LASTNAME\\0F."."""
        null_pos = -1
        for i, b in enumerate(data_bytes):
            if b == 0x00:
                null_pos = i
                break

        if null_pos < 0:
            # no separator: the whole field is the surname
            name = (
                bytes(b for b in data_bytes if 0x20 <= b <= 0x7E)
                .decode("ascii", errors="replace")
                .strip()
            )
            return name, ""

        last_bytes = data_bytes[:null_pos]
        first_bytes = data_bytes[null_pos + 1 :]

        # the forename ends at a null too
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
        """Read one player record and return its parsed fields."""
        if not self.data:
            return {}
        off = self._get_player_offset(team_index, player_slot)
        if off == 0 or off + PLAYER_SIZE > len(self.data):
            return {}

        d = self.data

        last_name, first_name = self._decode_name(d[off + OFF_NAME : off + OFF_NAME + NAME_LENGTH])

        position_byte = d[off + OFF_POSITION]
        position = BYTE_TO_POSITION.get(position_byte, f"?{position_byte}")

        ratings = list(d[off + OFF_RATINGS : off + OFF_RATINGS + RATING_COUNT])

        # season stats, 2-byte BE each
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
        """Read every player on a team."""
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
