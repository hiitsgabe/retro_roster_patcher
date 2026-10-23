"""ROM writer for KGJ MLB patcher.

Each player is exactly 32 bytes at a fixed offset; no variable-length records.

  - https://github.com/johnz1/ken_griffey_jr_presents_major_league_baseball_tools
"""

import os

from ...formats import snes
from .looks import write as write_looks
from .models import (
    CHAR_TO_BYTE,
    HR_DERBY_LIST,
    HR_DERBY_MARKER,
    HR_DERBY_TITLE,
    PLAYER_LENGTH,
    PLAYERS_PER_TEAM,
    POSITION_TO_BYTE,
    TEAM_COUNT,
    KGJPlayerRecord,
)
from .rom_reader import KGJRomReader, header_size


def _encode_char(ch: str) -> int:
    return CHAR_TO_BYTE.get(ch, CHAR_TO_BYTE.get(ch.upper(), 0x00))


def _encode_name(name: str, length: int) -> list[int]:
    result = []
    for ch in name[:length]:
        result.append(_encode_char(ch))
    while len(result) < length:
        result.append(0x00)  # SPACE
    return result


def _width(text: bytes) -> int:
    """Columns a run of name text takes: `FC lo hi` is one glyph."""
    return len(text) - 2 * text.count(0xFC)


def _encode_stat_pair(high: int, low: int) -> int:
    """Two ratings into high/low nibbles. The ROM stores (rating - 1), so 1-10
    maps to 0x0-0x9."""
    h = max(0, min(9, high - 1))
    lo = max(0, min(9, low - 1))
    return (h << 4) | lo


def _digits(value: int) -> int:
    """The high nibble the cartridge keeps next to a stat: its digit count - 1.
    The game never reads it; written so a record looks like the cartridge's."""
    return len(str(value)) - 1


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
    def __init__(self, rom_path: str, output_path: str, *, looks: bool = True):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = KGJRomReader(rom_path)
        # False keeps each slot's look bytes as the image has them.
        self.looks = looks

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
        return self.write_record(self.reader.get_player_offset(team_index, player_slot), player)

    def write_record(self, off: int, player: KGJPlayerRecord) -> bool:
        """One 32-byte record at file offset `off`; False when it would run off
        the end of the file."""
        if not self.data or off + PLAYER_LENGTH > len(self.data):
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

        # 0x0D: bats | throws, pitchers too
        d[off + 0x0D] = ((player.bats & 0xF) << 4) | (player.throws & 0xF)

        if player.is_pitcher:
            self._write_pitcher(off, player)
        else:
            self._write_batter(off, player)
        if self.looks:
            write_looks(d, off, player)

        return True

    def _write_batter(self, off: int, player: KGJPlayerRecord) -> None:
        assert self.data is not None
        d = self.data
        attrs = player.batter_attrs

        # 0x0B: BAT | POW
        d[off + 0x0B] = _encode_stat_pair(attrs.batting, attrs.power)

        # 0x0C: SPD | DEF
        d[off + 0x0C] = _encode_stat_pair(attrs.speed, attrs.defense)

        # 0x18-0x19: batting average, low byte then (digits | high nibble). The
        # cartridge's AVG digit nibble is always 3.
        avg_low, avg_high = _encode_split_stat(player.batting_avg)
        d[off + 0x18] = avg_low
        d[off + 0x19] = 0x30 | avg_high

        # 0x1A-0x1B: home runs
        hr = max(0, min(255, player.home_runs))
        d[off + 0x1A] = hr
        d[off + 0x1B] = _digits(hr) << 4

        # 0x1C-0x1D: RBI
        rbi = max(0, min(255, player.rbi))
        d[off + 0x1C] = rbi
        d[off + 0x1D] = _digits(rbi) << 4

        # 0x1E-0x1F: a batter has no saves; the cartridge writes 0 | 0xF0
        d[off + 0x1E] = 0x00
        d[off + 0x1F] = 0xF0

    def _write_pitcher(self, off: int, player: KGJPlayerRecord) -> None:
        assert self.data is not None
        d = self.data
        attrs = player.pitcher_attrs

        # 0x0B: SPD | CON
        d[off + 0x0B] = _encode_stat_pair(attrs.speed, attrs.control)

        # 0x0C: FAT - 1, the whole byte
        d[off + 0x0C] = max(0, min(9, attrs.fatigue - 1))

        # 0x15 hi: the throwing hand on the mound (the low nibble is a look)
        d[off + 0x15] = ((player.throws & 0xF) << 4) | (d[off + 0x15] & 0x0F)

        # 0x18-0x19: wins
        wins = max(0, min(255, player.wins))
        d[off + 0x18] = wins
        d[off + 0x19] = _digits(wins) << 4

        # 0x1A-0x1B: losses
        losses = max(0, min(255, player.losses))
        d[off + 0x1A] = losses
        d[off + 0x1B] = _digits(losses) << 4

        # 0x1C-0x1D: ERA, low byte then (digits | high nibble). The cartridge's
        # ERA digit nibble is always 2.
        era_low, era_high = _encode_split_stat(player.era)
        d[off + 0x1C] = era_low
        d[off + 0x1D] = 0x20 | era_high

        # 0x1E-0x1F: saves
        saves = max(0, min(255, player.saves))
        d[off + 0x1E] = saves
        d[off + 0x1F] = _digits(saves) << 4

    def write_team_roster(self, team_index: int, players: list[KGJPlayerRecord | None]) -> int:
        """Write a team's players and return how many were written. The list must
        be ordered:

          [0-14]  = batters (15)
          [15-19] = starting pitchers (5)
          [20-24] = relief pitchers (5)

        A `None` skips its slot, which keeps the ROM's record.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return -1

        written = 0
        for slot, player in enumerate(players[:PLAYERS_PER_TEAM]):
            if player is not None and self.write_player(team_index, slot, player):
                written += 1

        return written

    def write_derby(self, nl: list[KGJPlayerRecord], al: list[KGJPlayerRecord]) -> int:
        """The Home Run Derby's batters, up to 3 a league, after `HR_DERBY_MARKER`;
        how many were written. 0 when the image has no marker. A league given
        fewer keeps the cartridge's batters past them; the two derby pitchers
        are left alone."""
        if not self.data:
            return 0
        at = self.data.find(HR_DERBY_MARKER)
        if at < 0:
            return 0
        first = at + len(HR_DERBY_MARKER)
        written = 0
        names: list[str | None] = []
        for league, records in enumerate((nl, al)):
            for i, record in enumerate(records[:3]):
                off = first + (league * 3 + i) * PLAYER_LENGTH
                written += self.write_record(off, record)
            names += [f"{r.first_initial}. {r.last_name}" for r in records[:3]]
            names += [None] * (3 - len(records[:3]))
        self._derby_lists(names)
        self._derby_table(names)
        return written

    def _derby_lists(self, names: list[str | None]) -> None:
        """The six names on the derby's Select Player screen, each centred
        where its row was. Each list keeps its byte length (the script runs
        on): the room a shorter name frees becomes extra cursor codes."""
        assert self.data is not None
        d = self.data
        at = 0
        for league in range(2):
            at = d.find(HR_DERBY_LIST, at)
            if at < 0:
                return
            at += len(HR_DERBY_LIST)
            end = next((i for i in range(at, len(d)) if d[i] in (0xD0, 0xD2)), at)
            rows: list[tuple[int, int, bytes]] = []
            i = at
            while i < end and d[i] == 0xD1 and len(rows) < 3:
                j = i + 3
                while j < end and d[j] != 0xD1:
                    j += 3 if d[j] == 0xFC else 1
                rows.append((d[i + 1], d[i + 2], bytes(d[i + 3 : j]).rstrip(b"\x00")))
                i = j
            out = bytearray()
            cursor = b""
            for (x, y, old), name in zip(rows, names[league * 3 : league * 3 + 3], strict=False):
                text = old if name is None else bytes(_encode_name(name, len(name)))
                cursor = bytes([0xD1, max(0, (2 * x + _width(old) - _width(text)) // 2), y])
                out += cursor + text
            spare = end - at - len(out)
            if len(rows) == 3 and spare >= 0:
                d[at:end] = out + b"\x00" * (spare % 3) + cursor * (spare // 3)

    def _derby_table(self, names: list[str | None]) -> None:
        """The six names in the table after " HOMERUN DERBY ", centred in the
        cartridge's 14 columns (fewer when a string has less room). The game
        finds each string by its own pointer ($86:F36D on retail), so every
        string keeps its place: one the cartridge wrote longer (a glyph is 3
        bytes for one column) ends early, its spare bytes more D0s, which this
        walk skips on the next patch."""
        assert self.data is not None
        d = self.data
        at = d.find(HR_DERBY_TITLE)
        if at < 0:
            return
        i = at + len(HR_DERBY_TITLE)
        for name in names[:3] + [None] + names[3:]:
            end = d.find(0xD0, i)
            if end < 0:
                return
            while end + 1 < len(d) and d[end + 1] == 0xD0:
                end += 1
            if name is not None:
                cols = min(14, end - i)
                text = bytes(_encode_name(name[:cols].center(cols), cols))
                d[i:end] = text + b"\xd0" * (end - i - cols)
            i = end + 1

    def update_snes_checksum(self) -> None:
        """Rewrite the header checksum for everything written so far, over the
        image without its copier header. Run it after every other writer.

        Called by the orchestrator, not by `finalize`, which would otherwise stop
        being idempotent.
        """
        if not self.data or len(self.data) < 0x8000:
            return
        start = header_size(len(self.data))
        self.data[start:] = snes.fix_checksum(self.data[start:])

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
