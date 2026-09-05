"""ROM writer for KGJ MLB patcher.

Each player is exactly 32 bytes at a fixed offset; no variable-length records.

  - https://github.com/johnz1/ken_griffey_jr_presents_major_league_baseball_tools
"""

import os

from .models import (
    CHAR_TO_BYTE,
    PLAYER_LENGTH,
    PLAYERS_PER_TEAM,
    POSITION_TO_BYTE,
    TEAM_COUNT,
    KGJPlayerRecord,
)
from .rom_reader import ROM_SIZE_EXPECTED, KGJRomReader


def _encode_char(ch: str) -> int:
    return CHAR_TO_BYTE.get(ch, CHAR_TO_BYTE.get(ch.upper(), 0x00))


def _encode_name(name: str, length: int) -> list[int]:
    result = []
    for ch in name[:length]:
        result.append(_encode_char(ch))
    while len(result) < length:
        result.append(0x00)  # SPACE
    return result


def _encode_stat_pair(high: int, low: int) -> int:
    """Two ratings into high/low nibbles. The ROM stores (rating - 1), so 1-10
    maps to 0x0-0x9."""
    h = max(0, min(9, high - 1))
    lo = max(0, min(9, low - 1))
    return (h << 4) | lo


def _encode_split_stat(value: int) -> tuple[int, int]:
    """Split a 0-999 stat (batting avg x 1000, or ERA x 100) across a byte and a
    nibble. Plain binary, not BCD: .325 = 325 = 0x145 -> low_byte 0x45, high_nibble
    0x1, read back as `high * 256 + low`.

    The 999 clamp caps ERA at 9.99 and an average at .999, and floors a negative
    at 0.
    """
    value = max(0, min(999, value))
    low_byte = value & 0xFF
    high_nibble = (value >> 8) & 0x0F
    return low_byte, high_nibble


class KGJRomWriter:
    def __init__(self, rom_path: str, output_path: str):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = KGJRomReader(rom_path)

    def load(self) -> bool:
        if not self.reader.load():
            return False
        if not self.reader.validate():
            return False
        if self.reader.data:
            self.data = bytearray(self.reader.data)
            return True
        return False

    def write_player(self, team_index: int, player_slot: int, player: KGJPlayerRecord) -> bool:
        if not self.data or team_index >= TEAM_COUNT:
            return False
        if player_slot >= PLAYERS_PER_TEAM:
            return False

        off = self.reader.get_player_offset(team_index, player_slot)
        if off + PLAYER_LENGTH > len(self.data):
            return False

        d = self.data

        # 0x00: first initial
        d[off] = _encode_char(player.first_initial)

        # 0x01-0x08: surname, 8 chars, padded
        name_bytes = _encode_name(player.last_name, 8)
        for i, b in enumerate(name_bytes):
            d[off + 1 + i] = b

        # 0x09: position
        d[off + 0x09] = POSITION_TO_BYTE.get(player.position, 0x06)

        # 0x0A: jersey number
        d[off + 0x0A] = max(0, min(99, player.jersey_number))

        if player.is_pitcher:
            self._write_pitcher(off, player)
        else:
            self._write_batter(off, player)

        return True

    def _write_batter(self, off: int, player: KGJPlayerRecord) -> None:
        assert self.data is not None
        d = self.data
        attrs = player.batter_attrs
        app = player.batter_appearance

        # 0x0B: BAT | POW
        d[off + 0x0B] = _encode_stat_pair(attrs.batting, attrs.power)

        # 0x0C: SPD | DEF
        d[off + 0x0C] = _encode_stat_pair(attrs.speed, attrs.defense)

        # 0x0D: batting handedness
        d[off + 0x0D] = player.bat_hand

        # 0x0E: skin | head
        d[off + 0x0E] = ((app.skin & 0xF) << 4) | (app.head & 0xF)

        # 0x0F: hair colour | body
        d[off + 0x0F] = ((app.hair_color & 0xF) << 4) | (app.body & 0xF)

        # 0x10: legs size | legs stance
        d[off + 0x10] = ((app.legs_size & 0xF) << 4) | (app.legs_stance & 0xF)

        # 0x11: high nibble preserved | arms stance
        d[off + 0x11] = (d[off + 0x11] & 0xF0) | (app.arms_stance & 0xF)

        # 0x15-0x17: zero for batters
        d[off + 0x15] = 0x00
        d[off + 0x16] = 0x00
        d[off + 0x17] = 0x00

        # 0x18-0x19: batting average, low byte then high nibble
        avg_low, avg_high = _encode_split_stat(player.batting_avg)
        d[off + 0x18] = avg_low
        # 0x19: roster type | average hundreds
        d[off + 0x19] = (player.roster_type & 0xF0) | (avg_high & 0x0F)

        # 0x1A: home runs
        d[off + 0x1A] = max(0, min(255, player.home_runs))

        # 0x1B: always 0
        d[off + 0x1B] = 0x00

        # 0x1C: RBI
        d[off + 0x1C] = max(0, min(255, player.rbi))

        # 0x1D: batter flag
        d[off + 0x1D] = 0x10

        # 0x1E: unused for batters
        d[off + 0x1E] = 0x00

    def _write_pitcher(self, off: int, player: KGJPlayerRecord) -> None:
        assert self.data is not None
        d = self.data
        attrs = player.pitcher_attrs
        app = player.pitcher_appearance

        # 0x0B: SPD | CON
        d[off + 0x0B] = _encode_stat_pair(attrs.speed, attrs.control)

        # 0x0C: 0 | FAT
        d[off + 0x0C] = max(0, min(9, attrs.fatigue - 1)) & 0x0F

        # 0x0D: batting handedness -- pitchers bat too
        d[off + 0x0D] = player.bat_hand

        # 0x0E-0x11: batter appearance
        bapp = player.batter_appearance
        d[off + 0x0E] = ((bapp.skin & 0xF) << 4) | (bapp.head & 0xF)
        d[off + 0x0F] = ((bapp.hair_color & 0xF) << 4) | (bapp.body & 0xF)
        d[off + 0x10] = ((bapp.legs_size & 0xF) << 4) | (bapp.legs_stance & 0xF)
        d[off + 0x11] = (d[off + 0x11] & 0xF0) | (bapp.arms_stance & 0xF)

        # 0x15: pitching hand | pitching skin
        d[off + 0x15] = ((player.pitch_hand & 0xF) << 4) | (app.skin & 0xF)

        # 0x16: pitching head | pitching hair colour
        d[off + 0x16] = ((app.head & 0xF) << 4) | (app.hair_color & 0xF)

        # 0x17: pitching body | throwing style
        d[off + 0x17] = ((app.body & 0xF) << 4) | (app.throwing_style & 0xF)

        # 0x18: wins
        d[off + 0x18] = max(0, min(255, player.wins))

        # 0x19: roster type | 0
        d[off + 0x19] = player.roster_type & 0xF0

        # 0x1A: losses
        d[off + 0x1A] = max(0, min(255, player.losses))

        # 0x1B: always 0
        d[off + 0x1B] = 0x00

        # 0x1C-0x1D: ERA, low byte then high nibble
        era_low, era_high = _encode_split_stat(player.era)
        d[off + 0x1C] = era_low

        # 0x1D: pitcher flag 0x2 | ERA hundreds
        d[off + 0x1D] = 0x20 | (era_high & 0x0F)

        # 0x1E: saves
        d[off + 0x1E] = max(0, min(255, player.saves))

    def write_team_roster(self, team_index: int, players: list[KGJPlayerRecord]) -> int:
        """Write a team's players and return how many were written. The list must
        be ordered:

          [0-14]  = batters (15)
          [15-19] = starting pitchers (5)
          [20-24] = relief pitchers (5)

        `roster_type` is read, never assigned: `patcher.map_rosters` stamps it.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return -1

        written = 0
        for slot, player in enumerate(players[:PLAYERS_PER_TEAM]):
            if self.write_player(team_index, slot, player):
                written += 1

        return written

    def update_snes_checksum(self) -> None:
        """The checksum at 0x7FDE is the 16-bit sum of all ROM bytes; the
        complement at 0x7FDC is 0xFFFF minus it. Both shift by 512 on a headered
        image -- the one place in this package that needs the copier header, since
        every team-data offset comes from the marker search instead.

        Called by the orchestrator, not by `finalize`, which would otherwise stop
        being idempotent.
        """
        if not self.data or len(self.data) < 0x8000:
            return

        if len(self.data) == ROM_SIZE_EXPECTED + 512:
            cksum_off = 0x7FDE + 512
            comp_off = 0x7FDC + 512
        else:
            cksum_off = 0x7FDE
            comp_off = 0x7FDC

        # the fields must be zeroed before they are summed
        self.data[cksum_off] = 0x00
        self.data[cksum_off + 1] = 0x00
        self.data[comp_off] = 0xFF
        self.data[comp_off + 1] = 0xFF

        total = sum(self.data) & 0xFFFF

        self.data[cksum_off] = total & 0xFF
        self.data[cksum_off + 1] = (total >> 8) & 0xFF
        complement = (0xFFFF - total) & 0xFFFF
        self.data[comp_off] = complement & 0xFF
        self.data[comp_off + 1] = (complement >> 8) & 0xFF

    def finalize(self) -> bool:
        if not self.data:
            return False
        try:
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
