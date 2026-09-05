"""ROM writer for NBA Live 95 patcher.

Player records are variable-length: 69 fixed bytes plus a name. They are packed
adjacently, so a name must fit the gap to the next record. Big-endian (68000).

  - https://github.com/Team-95/rom-edit
"""

import os
import struct

from .models import (
    CHECKSUM_BYPASS_BYTES,
    CHECKSUM_BYPASS_OFFSET,
    OFF_EXPERIENCE,
    OFF_HAIR,
    OFF_HEIGHT,
    OFF_JERSEY,
    OFF_NAME,
    OFF_POSITION,
    OFF_RATINGS,
    OFF_SKIN,
    OFF_STATS,
    OFF_UNKNOWN2,
    OFF_WEIGHT,
    PLAYERS_PER_TEAM,
    RATING_COUNT,
    STAT_COUNT,
    TEAM_COUNT,
    TEAM_POINTER_SIZE,
    TEAM_ROSTER_ADDRESSES,
    NBALive95PlayerRecord,
)
from .rom_reader import NBALive95RomReader

# fixed portion of the record, before the name
FIXED_SIZE = OFF_NAME  # 0x45 = 69 bytes


def _encode_name_variable(last: str, first: str, max_bytes: int) -> bytes:
    """Encode a name into `max_bytes` as "Lastname\\0First", or "Lastname\\0F." when
    space is tight. Terminated by two consecutive nulls."""
    last_bytes = last.encode("ascii", errors="replace")
    first_bytes = first.encode("ascii", errors="replace")

    min_needed = len(last_bytes) + 1 + 2 + 2  # last + \0 + F. + \0\0

    if min_needed > max_bytes and len(last_bytes) > max_bytes - 5:
        last_bytes = last_bytes[: max(1, max_bytes - 5)]

    full_len = len(last_bytes) + 1 + len(first_bytes) + 2  # +2 for \0\0
    if full_len <= max_bytes:
        result = bytearray(full_len)
        result[: len(last_bytes)] = last_bytes
        pos = len(last_bytes)
        result[pos] = 0
        pos += 1
        result[pos : pos + len(first_bytes)] = first_bytes
        pos += len(first_bytes)
        result[pos] = 0
        result[pos + 1] = 0
        return bytes(result)

    abbrev_len = len(last_bytes) + 1 + 2 + 2  # last + \0 + F. + \0\0
    if abbrev_len <= max_bytes:
        result = bytearray(abbrev_len)
        result[: len(last_bytes)] = last_bytes
        pos = len(last_bytes)
        result[pos] = 0
        pos += 1
        # Reaching this branch requires `len(first_bytes) > 2`, so `first_bytes[0]`
        # cannot be out of range.
        result[pos] = first_bytes[0]
        result[pos + 1] = ord(".")
        pos += 2
        result[pos] = 0
        result[pos + 1] = 0
        return bytes(result)

    # last resort: surname only
    result = bytearray(min(len(last_bytes) + 2, max_bytes))
    # Upstream's behaviour, known wrong, preserved for byte fidelity: the two sides
    # of this slice are indexed differently, so at a budget of 2 the bytearray
    # resizes and `result[-2]` raises. No caller in this package can reach that --
    # limits are floored at 4. Do not "fix" the indices.
    result[: len(last_bytes)] = last_bytes[: len(result) - 2]
    result[-2] = 0
    result[-1] = 0
    return bytes(result)


class NBALive95RomWriter:
    """Only the fixed 69 bytes and the name are written, the name sized to fit the
    gap to the next record."""

    def __init__(self, rom_path: str, output_path: str):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = NBALive95RomReader(rom_path)
        self._record_limits: dict = {}  # (team, slot) -> max name bytes

    def load(self) -> bool:
        if not self.reader.load():
            return False
        if not self.reader.validate():
            return False
        if self.reader.data:
            self.data = bytearray(self.reader.data)
            self._compute_record_limits()
            return True
        return False

    def _compute_record_limits(self) -> None:
        """Records are packed, so the gap to the next player's offset is all the
        room a name has."""
        if not self.data:
            return

        for team_idx in range(TEAM_COUNT):
            team_addr = TEAM_ROSTER_ADDRESSES[team_idx]
            if team_addr == 0:
                continue

            ptrs = []
            for slot in range(PLAYERS_PER_TEAM):
                ptr = struct.unpack_from(">I", self.data, team_addr + slot * TEAM_POINTER_SIZE)[0]
                if ptr > 0:
                    ptrs.append((ptr, slot))

            ptrs.sort()

            for i, (ptr, slot) in enumerate(ptrs):
                if i + 1 < len(ptrs):
                    gap = ptrs[i + 1][0] - ptr
                else:
                    # last player: fall back to the original record's own length
                    gap = self._original_record_size(ptr)

                max_name = gap - FIXED_SIZE
                self._record_limits[(team_idx, slot)] = max(4, max_name)

    def _original_record_size(self, ptr: int) -> int:
        """The record ends at the name's two-null terminator."""
        if not self.data:
            return FIXED_SIZE + 10
        pos = ptr + OFF_NAME
        zero_count = 0
        while pos < len(self.data) and zero_count < 2:
            if self.data[pos] == 0:
                zero_count += 1
            else:
                zero_count = 0
            pos += 1
        return pos - ptr

    def apply_patches(self) -> None:
        """Bypass the game's internal checksum verification."""
        if not self.data:
            return
        if CHECKSUM_BYPASS_OFFSET + len(CHECKSUM_BYPASS_BYTES) <= len(self.data):
            self.data[
                CHECKSUM_BYPASS_OFFSET : CHECKSUM_BYPASS_OFFSET + len(CHECKSUM_BYPASS_BYTES)
            ] = CHECKSUM_BYPASS_BYTES

    def write_player(
        self, team_index: int, player_slot: int, player: NBALive95PlayerRecord
    ) -> bool:
        if not self.data or team_index >= TEAM_COUNT:
            return False
        if player_slot >= PLAYERS_PER_TEAM:
            return False

        off = self.reader._get_player_offset(team_index, player_slot)
        if off == 0 or off + FIXED_SIZE > len(self.data):
            return False

        d = self.data

        # fixed fields, 0x00-0x44
        d[off + OFF_JERSEY] = max(0, min(99, player.jersey))
        d[off + OFF_POSITION] = max(0, min(4, player.position))
        d[off + OFF_HEIGHT] = max(0, min(255, player.height_inches))
        d[off + OFF_WEIGHT] = max(0, min(255, player.weight_lbs - 100))
        d[off + OFF_EXPERIENCE] = max(0, min(255, player.experience))

        # Upstream's behaviour, known wrong, preserved for byte fidelity: nothing
        # supplies either field, so every patched player is written with skin tone 0
        # and hair style 0 over whatever the 1994 image had. Do not re-add the `> 0`
        # guards that skipped the two writes.
        d[off + OFF_SKIN] = max(0, min(3, player.skin_color))
        d[off + OFF_HAIR] = max(0, min(0x26, player.hair_style))

        # season stats, 17 x 2-byte BE; clamped because `>H` would otherwise raise
        # `struct.error` on a total above 65 535
        for i in range(STAT_COUNT):
            stat_val = player.season_stats[i] if i < len(player.season_stats) else 0
            struct.pack_into(">H", d, off + OFF_STATS + i * 2, max(0, min(0xFFFF, stat_val)))

        d[off + OFF_UNKNOWN2] = 0x00

        # ratings, 16 x 1 byte on a 0-99 scale
        for i in range(RATING_COUNT):
            rating = player.ratings[i] if i < len(player.ratings) else 50
            d[off + OFF_RATINGS + i] = max(0, min(99, rating))

        # bytes 0x3B-0x44 are unknown and left alone

        max_name = self._record_limits.get((team_index, player_slot), 24)
        name_bytes = _encode_name_variable(player.name_last, player.name_first, max_name)
        d[off + OFF_NAME : off + OFF_NAME + len(name_bytes)] = name_bytes

        return True

    def write_team_roster(self, team_index: int, players: list[NBALive95PlayerRecord]) -> int:
        """Write a team's players and return how many were written."""
        if not self.data or team_index >= TEAM_COUNT:
            return -1

        written = 0
        for slot, player in enumerate(players[:PLAYERS_PER_TEAM]):
            if self.write_player(team_index, slot, player):
                written += 1

        return written

    def _fix_checksum(self) -> None:
        """The header checksum at 0x18E is the 16-bit sum of every big-endian word
        from 0x200 to the end of the ROM."""
        if not self.data or len(self.data) < 0x200:
            return
        total = 0
        for i in range(0x200, len(self.data), 2):
            if i + 1 < len(self.data):
                total += struct.unpack_from(">H", self.data, i)[0]
        struct.pack_into(">H", self.data, 0x18E, total & 0xFFFF)

    def finalize(self) -> bool:
        if not self.data:
            return False
        try:
            self._fix_checksum()
            output_dir = os.path.dirname(self.output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            with open(self.output_path, "wb") as f:
                f.write(self.data)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception:
            return False
