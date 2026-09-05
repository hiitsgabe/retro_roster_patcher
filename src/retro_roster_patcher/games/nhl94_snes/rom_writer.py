"""ROM writer for NHL94 SNES patcher.

Patches in place: names are truncated to fit the original record's space, and the
team header, strings and structure are preserved.

  - https://github.com/clandrew/nhl94e
  - https://cml-a.com/content/2020/11/23/names-and-stats-in-nhl-94/

No checksum handling, deliberately: the SNES does not verify the header word at
$FFDC/$FFDE and this game does not read it. The Genesis sibling does both.
"""

import os
from collections.abc import Callable

from .models import (
    TEAM_COUNT,
    NHL94PlayerRecord,
)
from .rom_reader import (
    PLAYER_COUNT_OFFSET,
    STATS_SIZE,
    NHL94SNESRomReader,
)

# Team-data header: byte 17 player count, byte 18 team overall,
# bytes 19..74 the 8 lines x 7 slots = 56 bytes of line assignments.
LINE_ASSIGN_OFFSET = 19
LINE_SLOTS = 7  # G, LD, RD, LW, C, RW, EA
LINE_COUNT = 8  # SC1, SC2, CHK, PP1, PP2, PK1, PK2, EA

# The line table is built from `g1 = 0`, `g2 = 1` and `f_base = 2`, so it always
# assumes two goalie records precede the forwards.
HEADER_GOALIE_SLOTS = 2


def header_counts(written: int, num_forwards: int, num_defensemen: int) -> tuple[int, int]:
    """The `(forwards, defencemen)` a header may claim for `written` records.

    The line table indexes players by absolute position -- forwards at
    `HEADER_GOALIE_SLOTS`, defencemen at `HEADER_GOALIE_SLOTS + forwards` -- so a
    header claiming more than reached the image names records the writer never
    wrote and the game reads the zero-fill as a player. Clamping to the prefix
    actually written is a no-op for any full roster.
    """
    forwards = min(max(written - HEADER_GOALIE_SLOTS, 0), num_forwards)
    defensemen = min(max(written - HEADER_GOALIE_SLOTS - num_forwards, 0), num_defensemen)
    return forwards, defensemen


def encode_nibble(high: int, low: int) -> int:
    high = max(0, min(6, high))
    low = max(0, min(6, low))
    return (high << 4) | low


def encode_weight_nibble(weight_class: int, low_stat: int) -> int:
    """Weight class (0-14) in the high nibble, a 0-6 stat in the low one."""
    weight_class = max(0, min(14, weight_class))
    low_stat = max(0, min(6, low_stat))
    return (weight_class << 4) | low_stat


class NHL94SNESRomWriter:
    def __init__(self, rom_path: str, output_path: str):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = NHL94SNESRomReader(rom_path)

    def load(self) -> bool:
        if not self.reader.load():
            return False

        if self.reader.data:
            self.data = bytearray(self.reader.data)
            return True
        return False

    def _get_team_player_region(self, team_index: int) -> tuple[int, int]:
        """Offset and size of a team's player records, up to and including the
        2-byte terminator."""
        file_off = self.reader._read_team_pointer(team_index)
        if file_off is None or not self.data:
            return 0, 0

        start = self.reader._skip_team_header(file_off)
        offset = start

        while offset < len(self.data) - 1:
            length = self.data[offset] | (self.data[offset + 1] << 8)
            if length < 3:  # terminator
                offset += 2
                break
            offset += length + STATS_SIZE

        return start, offset - start

    def write_team_roster(
        self,
        team_index: int,
        players: list[NHL94PlayerRecord],
    ) -> int:
        """Write a team's player records into its existing region. Names are
        truncated to fit and the leftover space is zero-filled. Returns how many
        records reached the image, or -1 on error."""
        if not self.data or team_index >= TEAM_COUNT:
            return -1

        start, region_size = self._get_team_player_region(team_index)
        if region_size == 0:
            return -1

        offset = start
        end = start + region_size
        written = 0

        for player in players:
            # 2 (length) + name + 8 (stats), leaving 2 for the terminator
            max_name_for_record = (end - offset) - 2 - STATS_SIZE - 2
            if max_name_for_record < 1:
                break

            # Upstream's behaviour, known wrong, preserved for byte fidelity: an empty
            # name writes a length word of 2, which the readers treat as the end-of-roster
            # terminator, burying the rest of the roster. Do not re-add the `or b"?"`.
            name = player.name[:max_name_for_record]
            name_bytes = name.encode("ascii", errors="replace")
            name_len = len(name_bytes)

            # 2-byte LE length, itself included
            total_len = name_len + 2
            self.data[offset] = total_len & 0xFF
            self.data[offset + 1] = (total_len >> 8) & 0xFF
            offset += 2

            for i, b in enumerate(name_bytes):
                self.data[offset + i] = b
            offset += name_len

            offset = self._write_player_stats(player, offset)
            written += 1

        # terminator: 0x02 0x00, an empty string
        if offset + 2 <= end:
            self.data[offset] = 0x02
            self.data[offset + 1] = 0x00
            offset += 2

        while offset < end:
            self.data[offset] = 0x00
            offset += 1

        return written

    def write_team_header(
        self,
        team_index: int,
        num_forwards: int,
        num_defensemen: int,
    ) -> bool:
        """Update byte 17 (the player-count nibbles) and bytes 19-74 (8 lines x 7
        slots) so the game's line display matches the G+F+D order written.

        The counts must be the ones that reached the image, not the ones the
        selection asked for; `header_counts` turns one into the other.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return False

        file_off = self.reader._read_team_pointer(team_index)
        if file_off is None:
            return False

        pc_off = file_off + PLAYER_COUNT_OFFSET
        if pc_off >= len(self.data):
            return False
        nf = min(15, max(0, num_forwards))
        nd = min(15, max(0, num_defensemen))
        self.data[pc_off] = (nf << 4) | nd

        # Player indices:
        #   G:  0, 1
        #   F:  2  .. 2+nf-1    (LW,C,RW per line)
        #   D:  2+nf .. 2+nf+nd-1
        g1, g2 = 0, min(1, 1)
        f_base = HEADER_GOALIE_SLOTS
        d_base = HEADER_GOALIE_SLOTS + nf

        def fi(i: int) -> int:
            return min(f_base + i, f_base + nf - 1)

        def di(i: int) -> int:
            return min(d_base + i, d_base + nd - 1)

        lines = self._build_lines(
            g1,
            g2,
            f_base,
            nf,
            d_base,
            nd,
            fi,
            di,
        )

        la_off = file_off + LINE_ASSIGN_OFFSET
        if la_off + LINE_COUNT * LINE_SLOTS > len(self.data):
            return False

        for line_bytes in lines:
            for b in line_bytes:
                self.data[la_off] = b & 0xFF
                la_off += 1

        return True

    @staticmethod
    def _build_lines(
        g1: int,
        g2: int,
        fb: int,
        nf: int,
        db: int,
        nd: int,
        fi: Callable[[int], int],
        di: Callable[[int], int],
    ) -> list[list[int]]:
        """Build 8 line configs: SC1 SC2 CHK PP1 PP2 PK1 PK2 EA.

        Each line = [G, LD, RD, LW, C, RW, EA].
        """
        sc1 = [g1, di(0), di(1), fi(0), fi(1), fi(2), fi(0)]
        sc2 = [g1, di(2), di(3), fi(3), fi(4), fi(5), fi(3)]
        sc3 = [g1, di(4), di(5), fi(6), fi(7), fi(8), fi(6)]  # CHK
        pp1 = [g1, di(0), di(1), fi(0), fi(1), fi(2), fi(3)]
        pp2 = [g1, di(2), di(3), fi(3), fi(4), fi(5), fi(6)]
        pk1 = [g1, di(0), di(1), fi(6), fi(7), fi(8), fi(6)]
        pk2 = [g1, di(2), di(3), fi(3), fi(4), fi(5), fi(3)]
        ea = [g2, di(0), di(1), fi(0), fi(1), fi(2), fi(3)]  # backup G pulled for a forward

        return [sc1, sc2, sc3, pp1, pp2, pk1, pk2, ea]

    def _write_player_stats(self, player: NHL94PlayerRecord, offset: int) -> int:
        """Write a player's 8 stat bytes and return the offset after them.

        Byte 0: Jersey number (BCD)
        Byte 1: Weight class (0-14) | Agility (0-6)
        Byte 2: Speed (0-6) | Off. Awareness (0-6)
        Byte 3: Def. Awareness (0-6) | Shot Power (0-6)
        Byte 4: Checking (0-6) | Handedness (0=L, 1=R)
        Byte 5: Stick Handling (0-6) | Shot Accuracy (0-6)
        Byte 6: Endurance (0-6) | Roughness (0-6)
        Byte 7: Pass Accuracy (0-6) | Aggression (0-6)

        Upstream's behaviour, known wrong, preserved for byte fidelity: the
        out-of-range branch returns `offset` unchanged, which the caller cannot
        tell from success. Unreachable in practice -- such a region makes the
        caller's zero-fill raise first. Do not "fix" it.
        """
        if not self.data or offset + STATS_SIZE > len(self.data):
            return offset

        attrs = player.attributes

        jersey = max(1, min(99, player.jersey_number))
        self.data[offset] = ((jersey // 10) << 4) | (jersey % 10)
        offset += 1

        self.data[offset] = encode_weight_nibble(player.weight_class, attrs.agility)
        offset += 1

        self.data[offset] = encode_nibble(attrs.speed, attrs.off_awareness)
        offset += 1

        self.data[offset] = encode_nibble(attrs.def_awareness, attrs.shot_power)
        offset += 1

        self.data[offset] = encode_nibble(attrs.checking, player.handedness)
        offset += 1

        self.data[offset] = encode_nibble(attrs.stick_handling, attrs.shot_accuracy)
        offset += 1

        self.data[offset] = encode_nibble(attrs.endurance, attrs.roughness)
        offset += 1

        self.data[offset] = encode_nibble(attrs.pass_accuracy, attrs.aggression)
        offset += 1

        return offset

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
