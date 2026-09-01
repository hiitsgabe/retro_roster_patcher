"""ROM writer for NHL94 Genesis patcher.

Writes player names and stats back to NHL94 Genesis ROM.
Does in-place patching — names are truncated to fit the original
record's space. Team header, strings, palettes, and structure are preserved.

Also disables the ROM checksum so edited ROMs boot correctly.

References:
  - https://forum.nhl94.com/index.php?/topic/26353-how-to-manually-edit-the-team-player-data-nhl-94/
  - https://nhl94.com/html/editing/edit_bin.php
"""

import os

from .models import (
    TEAM_COUNT,
    NHL94GenPlayerRecord,
)
from .rom_reader import (
    CHECKSUM_BYPASS_OFFSET,
    STATS_SIZE,
    NHL94GenesisRomReader,
)


def encode_nibble(high: int, low: int) -> int:
    """Encode two nibbles (0-6) into a byte."""
    high = max(0, min(6, high))
    low = max(0, min(6, low))
    return (high << 4) | low


def encode_weight_nibble(weight_class: int, low_stat: int) -> int:
    """Encode weight class (0-14) in high nibble + stat (0-6) in low nibble."""
    weight_class = max(0, min(14, weight_class))
    low_stat = max(0, min(6, low_stat))
    return (weight_class << 4) | low_stat


class NHL94GenesisRomWriter:
    """Writes player data to NHL94 Genesis ROM.

    Strategy: in-place patching. For each team, read the existing player
    region to know how much space is available, then write new records
    that fit within that space. Names are truncated if needed.
    """

    def __init__(self, rom_path: str, output_path: str):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = NHL94GenesisRomReader(rom_path)

    def load(self) -> bool:
        """Load ROM data for writing."""
        if not self.reader.load():
            return False
        if self.reader.data:
            self.data = bytearray(self.reader.data)
            return True
        return False

    def _write_u16_be(self, offset: int, value: int):
        """Write a big-endian 16-bit value."""
        assert self.data is not None
        self.data[offset] = (value >> 8) & 0xFF
        self.data[offset + 1] = value & 0xFF

    def disable_checksum(self):
        """Disable ROM checksum by writing RTS (0x4E75) at bypass offset.

        This makes the checksum routine return immediately so edited ROMs boot.
        """
        if not self.data:
            return
        if CHECKSUM_BYPASS_OFFSET + 2 <= len(self.data):
            self.data[CHECKSUM_BYPASS_OFFSET] = 0x4E
            self.data[CHECKSUM_BYPASS_OFFSET + 1] = 0x75

    def update_header_checksum(self):
        """Recalculate the Genesis ROM header checksum at 0x18E.

        The checksum is the sum of all 16-bit big-endian words from
        offset 0x200 to end of ROM, stored as a 16-bit value at 0x18E.
        """
        if not self.data or len(self.data) < 0x200:
            return
        checksum = 0
        for i in range(0x200, len(self.data), 2):
            if i + 1 < len(self.data):
                word = (self.data[i] << 8) | self.data[i + 1]
            else:
                word = self.data[i] << 8
            checksum = (checksum + word) & 0xFFFF
        self.data[0x18E] = (checksum >> 8) & 0xFF
        self.data[0x18F] = checksum & 0xFF

    def write_team_roster(
        self,
        team_index: int,
        players: list[NHL94GenPlayerRecord],
    ) -> int:
        """Write player records for a team, fitting within existing space.

        Names are truncated if they don't fit. Excess space is zero-filled.
        Returns the number of players actually written, or -1 on error.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return -1

        start, region_size = self.reader.get_team_player_region(team_index)
        if region_size == 0:
            return -1

        offset = start
        end = start + region_size
        written = 0

        for player in players:
            # Space needed: 2 (length) + name_len + 8 (stats) + 2 (sentinel)
            max_name_len = (end - offset) - 2 - STATS_SIZE - 2
            if max_name_len < 1:
                break  # No room for more players

            # Truncate name to fit. An empty name would encode a length word of
            # 2, which both `read_team_roster` and `get_team_player_region` treat
            # as the end-of-roster sentinel, so it would hide every record after
            # it while this method still counted them. One placeholder byte keeps
            # the length word at 3 — the low edge of the reader's test — and the
            # record chain intact.
            name = player.name[:max_name_len]
            name_bytes = name.encode("ascii", errors="replace") or b"?"
            name_len = len(name_bytes)

            # Write 2-byte BE length (includes the 2 length bytes)
            total_len = name_len + 2
            self._write_u16_be(offset, total_len)
            offset += 2

            # Write name bytes
            for i, b in enumerate(name_bytes):
                self.data[offset + i] = b
            offset += name_len

            # Write 8 stat bytes
            offset = self._write_player_stats(player, offset)
            written += 1

        # Write end-of-roster sentinel (0x0000)
        if offset + 2 <= end:
            self.data[offset] = 0x00
            self.data[offset + 1] = 0x00
            offset += 2

        # Zero-fill remaining space
        while offset < end:
            self.data[offset] = 0x00
            offset += 1

        return written

    def _write_player_stats(self, player: NHL94GenPlayerRecord, offset: int) -> int:
        """Write 8 stat bytes for a player. Returns new offset.

        Byte layout (a BCD jersey byte, then 14 nibbles packed into 7 bytes):
          Byte 0: Jersey number (BCD)
          Byte 1: Weight (0-14) | Agility (0-6)
          Byte 2: Speed (0-6) | Off. Awareness (0-6)
          Byte 3: Def. Awareness (0-6) | Shot Power (0-6)
          Byte 4: Checking (0-6) | Handedness (0=L, 1=R)
          Byte 5: Stick Handling (0-6) | Shot Accuracy (0-6)
          Byte 6: Endurance (0-6) | Roughness (0-6)
          Byte 7: Pass Accuracy (0-6) | Aggression (0-6)
        """
        if not self.data or offset + STATS_SIZE > len(self.data):
            return offset

        attrs = player.attributes

        # Byte 0: Jersey number (BCD)
        jersey = max(1, min(99, player.jersey_number))
        self.data[offset] = ((jersey // 10) << 4) | (jersey % 10)
        offset += 1

        # Byte 1: Weight class | Agility
        self.data[offset] = encode_weight_nibble(player.weight_class, attrs.agility)
        offset += 1

        # Byte 2: Speed | Off. Awareness
        self.data[offset] = encode_nibble(attrs.speed, attrs.off_awareness)
        offset += 1

        # Byte 3: Def. Awareness | Shot Power
        self.data[offset] = encode_nibble(attrs.def_awareness, attrs.shot_power)
        offset += 1

        # Byte 4: Checking | Handedness
        self.data[offset] = encode_nibble(attrs.checking, player.handedness)
        offset += 1

        # Byte 5: Stick Handling | Shot Accuracy
        self.data[offset] = encode_nibble(attrs.stick_handling, attrs.shot_accuracy)
        offset += 1

        # Byte 6: Endurance | Roughness
        self.data[offset] = encode_nibble(attrs.endurance, attrs.roughness)
        offset += 1

        # Byte 7: Pass Accuracy | Aggression
        self.data[offset] = encode_nibble(attrs.pass_accuracy, attrs.aggression)
        offset += 1

        return offset

    def write_team_header(
        self,
        team_index: int,
        players: list[NHL94GenPlayerRecord],
        actual_count: int = -1,
    ) -> bool:
        """Write count byte, goalie bytes, and line assignments.

        Must be called AFTER write_team_roster() for the same team.
        Analyzes the player list to determine position counts and
        generates proper line assignments with correct roster indices.

        ROM roster order: goalies first, then forwards, then defense.
        The count byte tells the game how many F and D there are;
        goalies = total - F - D (always at the start of the roster).

        If actual_count is provided, only the first actual_count players
        are considered (matching what actually fit in ROM).
        """
        if not self.data or team_index >= TEAM_COUNT or not players:
            return False

        offsets = self.reader.get_team_section_offsets(team_index)
        if offsets is None:
            return False

        # Slice to only the players that actually fit in ROM
        if actual_count >= 0:
            players = players[:actual_count]
        if not players:
            return False

        # Count positions
        goalie_count = sum(1 for p in players if p.is_goalie)
        defense_count = sum(1 for p in players if p.position == "D")
        forward_count = len(players) - goalie_count - defense_count

        # Write count byte at ratings + 3
        # High nibble = forward count, low nibble = defense count
        f_nibble = min(15, forward_count)
        d_nibble = min(15, defense_count)
        self.data[offsets["ratings"] + 3] = (f_nibble << 4) | d_nibble

        # Write goalie byte 1 only — preserve original byte 0 (per-team value)
        self.data[offsets["goalies"] + 1] = 0x00 if goalie_count <= 2 else 0x10

        # Generate and write lines (8 lines × 8 bytes = 64 bytes)
        lines = self._generate_lines(
            goalie_count,
            forward_count,
            defense_count,
        )
        lines_off = offsets["lines"]
        for i, line in enumerate(lines):
            for j, val in enumerate(line):
                self.data[lines_off + i * 8 + j] = val

        return True

    def _generate_lines(
        self,
        goalie_count: int,
        forward_count: int,
        defense_count: int,
    ) -> list[list[int]]:
        """Generate 8 lines of 8 bytes each for team line assignments.

        Line format: [01, LD, RD, LW, C, RW, EA, G]
        Roster layout: [G1,G2,..., C1,LW1,RW1, C2,LW2,RW2,..., D1,D2,...]

        Lines 0-3: Even strength (4 forward lines + 3 D pairs rotating)
        Lines 4-5: Power play (top players)
        Lines 6-7: Penalty kill (3rd/4th line forwards, top D)
        """
        g_start = 0
        f_start = goalie_count
        d_start = goalie_count + forward_count

        starter = g_start if goalie_count > 0 else 0

        def f(i: int) -> int:
            """Get forward roster index, clamped to valid range."""
            if forward_count == 0:
                return starter
            return f_start + min(i, forward_count - 1)

        def d(i: int) -> int:
            """Get defense roster index, clamped to valid range."""
            if defense_count == 0:
                return f(0)
            return d_start + min(i, defense_count - 1)

        lines: list[list[int]] = []

        # Even-strength lines 0-3
        # Forwards in groups of 3: (C, LW, RW) per line
        # Defense pairs rotate through available pairs
        d_pairs = max(1, defense_count // 2)
        for li in range(4):
            c = f(li * 3)
            lw = f(li * 3 + 1)
            rw = f(li * 3 + 2)
            pair = li % d_pairs
            ld = d(pair * 2)
            rd = d(pair * 2 + 1)
            ea = f(((li + 1) % 4) * 3)  # Next line's center
            lines.append([0x01, ld, rd, lw, c, rw, ea, starter])

        # Power play lines 4-5 (best offensive players)
        for pp in range(2):
            c = f(pp * 3)
            lw = f(pp * 3 + 1)
            rw = f(pp * 3 + 2)
            ld = d(pp * 2)
            rd = d(pp * 2 + 1)
            ea = f(((pp + 1) * 3) + 1)  # Another top forward
            lines.append([0x01, ld, rd, lw, c, rw, ea, starter])

        # Penalty kill lines 6-7 (checking forwards, top D)
        for pk in range(2):
            # Use 3rd and 4th line forwards for PK
            pk_line = pk + 2
            c = f(pk_line * 3)
            lw = f(pk_line * 3 + 1)
            rw = f(pk_line * 3 + 2)
            ld = d(pk * 2)
            rd = d(pk * 2 + 1)
            ea = f(pk * 3)  # Top-line center as EA
            lines.append([0x01, ld, rd, lw, c, rw, ea, starter])

        return lines

    def finalize(self) -> bool:
        """Write the modified ROM to output path."""
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
