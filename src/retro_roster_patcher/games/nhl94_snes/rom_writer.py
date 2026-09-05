"""ROM writer for NHL94 SNES patcher.

Writes player names and stats back to NHL94 SNES ROM.
Does in-place patching - names are truncated to fit the original
record's space. Team header, strings, and structure are preserved.

References:
  - https://github.com/clandrew/nhl94e
  - https://cml-a.com/content/2020/11/23/names-and-stats-in-nhl-94/

Nothing here touches the SNES header's checksum word at $FFDC/$FFDE, and
nothing bypasses a checksum test in the game code, because NHL 94 on the SNES
runs neither: the console does not verify the header and the game does not read
it. Its Genesis sibling does both, so the absence is deliberate rather than
missing. `games/nhl94_snes/patcher.py` says what it costs.
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

# Line assignment offset within team data header.
# Byte 17 = player count, Byte 18 = team overall,
# Bytes 19..74 = 8 lines x 7 slots = 56 bytes.
LINE_ASSIGN_OFFSET = 19
LINE_SLOTS = 7  # G, LD, RD, LW, C, RW, EA
LINE_COUNT = 8  # SC1, SC2, CHK, PP1, PP2, PK1, PK2, EA

# How many records the line table assumes precede the forwards. `write_team_header`
# builds every line from `g1 = 0`, `g2 = 1` and `f_base = 2`, so the layout it
# writes is two goalies and then the forwards, whatever the selection was cut to.
# Named because `header_counts` has to make the same assumption to stay
# consistent with it, and because it is where the layout stops being a variable.
HEADER_GOALIE_SLOTS = 2


def header_counts(written: int, num_forwards: int, num_defensemen: int) -> tuple[int, int]:
    """The `(forwards, defensemen)` a header may claim for `written` records.

    DELIBERATE DIVERGENCE. Upstream -- and this port until now -- wrote the
    header from the counts the *selection* was cut to, never from the number of
    records that reached the image. The two disagree whenever the roster was
    short of the shape asked for, and the header's line table indexes players by
    absolute position: forwards at `HEADER_GOALIE_SLOTS`, defensemen at
    `HEADER_GOALIE_SLOTS + forwards`, with `_build_lines` clamping each side to
    its own last index. So a team that produced 21 records under a 2/14/7
    request got a header claiming 7 defensemen, `di(5)` resolving to record 21,
    and a line table naming a record the writer never wrote. The game then reads
    whatever the zero-fill left there as a player.

    Clamping to the prefix that was actually written keeps the boundary in the
    same place -- the list really is goalies, then forwards, then defensemen, in
    that order -- while making `HEADER_GOALIE_SLOTS + forwards + defensemen`
    equal `written` for any `written >= HEADER_GOALIE_SLOTS`, which is exactly
    the condition that stops `_build_lines` naming an absent record.

    Byte-identical to the old behaviour whenever nothing was lost, which is
    every full roster: `written == HEADER_GOALIE_SLOTS + num_forwards +
    num_defensemen` returns `(num_forwards, num_defensemen)` unchanged.

    Two things this does NOT fix, both out of the filed defect's scope and both
    pinned by tests rather than left to be rediscovered:

      * `written < HEADER_GOALIE_SLOTS` still yields a line table naming record
        1, because `_build_lines` clamps a zero-length side to `base - 1`. One
        record is not a hockey team and `patcher.patch` has no better answer
        than the one it already gives for zero.
      * the list's goalie prefix need not be `HEADER_GOALIE_SLOTS` long.
        `stat_mapper.select_roster` puts fewer than `num_goalies` goalies first
        when a provider is short of them, and `patcher._resolve_roster_counts`
        accepts any non-negative goalie count from `RomInfo.extra`, so the
        prefix can be shorter or longer than two. Either way the forwards do not
        start where the line table looks for them and the whole table is off by
        the difference. That is a defect in the selection's shape, not in this
        arithmetic, and no count passed here can repair it. The clamps do at
        least stop a long goalie prefix being reported as extra defencemen.
    """
    forwards = min(max(written - HEADER_GOALIE_SLOTS, 0), num_forwards)
    defensemen = min(max(written - HEADER_GOALIE_SLOTS - num_forwards, 0), num_defensemen)
    return forwards, defensemen


def encode_nibble(high: int, low: int) -> int:
    """Encode two nibbles (0-6) into a byte."""
    high = max(0, min(6, high))
    low = max(0, min(6, low))
    return (high << 4) | low


def encode_weight_nibble(weight_class: int, low_stat: int) -> int:
    """Encode weight class (0-14) in high nibble + stat (0-6) in low nibble.

    Weight class uses the full 4-bit range (0-15), not the 0-6 stat range.
    """
    weight_class = max(0, min(14, weight_class))
    low_stat = max(0, min(6, low_stat))
    return (weight_class << 4) | low_stat


class NHL94SNESRomWriter:
    """Writes player data to NHL94 SNES ROM.

    Strategy: in-place patching. For each team, we read the existing
    player records to know how much space is available, then write
    new records that fit within that space. Names are truncated if needed.
    """

    def __init__(self, rom_path: str, output_path: str):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = NHL94SNESRomReader(rom_path)

    def load(self) -> bool:
        """Load ROM data for writing."""
        if not self.reader.load():
            return False

        # Make a writable copy
        if self.reader.data:
            self.data = bytearray(self.reader.data)
            return True
        return False

    def _get_team_player_region(self, team_index: int) -> tuple[int, int]:
        """Get the file offset and total byte size of a team's player region.

        Returns (start_offset, total_bytes) where start_offset is the
        first byte of the first player record and total_bytes includes
        all player records + the 2-byte terminator.
        """
        file_off = self.reader._read_team_pointer(team_index)
        if file_off is None or not self.data:
            return 0, 0

        # Skip header
        start = self.reader._skip_team_header(file_off)
        offset = start

        while offset < len(self.data) - 1:
            length = self.data[offset] | (self.data[offset + 1] << 8)
            if length < 3:  # Terminator
                offset += 2  # Include terminator in region
                break
            offset += length + STATS_SIZE

        return start, offset - start

    def write_team_roster(
        self,
        team_index: int,
        players: list[NHL94PlayerRecord],
    ) -> int:
        """Write player records for a team, fitting within existing space.

        Names are truncated if they don't fit. Excess space is zero-filled.
        Returns the number of players actually written, or -1 on error.

        DELIBERATE DIVERGENCE: upstream returned `bool`, and its caller then
        added `len(players)` to `players_patched`. That number is not what
        reached the image -- this method stops as soon as the next record would
        not fit and drops every player after it, silently, while still returning
        True. `core.models.PatchResult` defines `players_patched` as the records
        that reached the ROM, so the count is returned from the one place that
        knows it. Not one byte written changes; only what is reported about them.
        The Genesis sibling's writer already returns this count.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return -1

        start, region_size = self._get_team_player_region(team_index)
        if region_size == 0:
            return -1

        offset = start
        end = start + region_size
        written = 0

        for player in players:
            # Calculate space needed: 2 (length) + name_len + 8 (stats)
            # Plus we need at least 2 bytes left for the terminator
            max_name_for_record = (end - offset) - 2 - STATS_SIZE - 2
            if max_name_for_record < 1:
                break  # No room for more players

            # Truncate name to fit.
            #
            # DELIBERATE DIVERGENCE, the same one already made in
            # `games/nhl94_genesis/rom_writer.py`: an empty name encodes a length
            # word of 2, and both `read_team_roster` and `_get_team_player_region`
            # stop at any length below 3. Upstream would write it, hiding every
            # record after it behind a terminator while still reporting them all
            # written. One placeholder byte keeps the length word at 3 and the
            # record chain intact.
            name = player.name[:max_name_for_record]
            name_bytes = name.encode("ascii", errors="replace") or b"?"
            name_len = len(name_bytes)

            # Write 2-byte LE length (includes the 2 length bytes)
            total_len = name_len + 2
            self.data[offset] = total_len & 0xFF
            self.data[offset + 1] = (total_len >> 8) & 0xFF
            offset += 2

            # Write name
            for i, b in enumerate(name_bytes):
                self.data[offset + i] = b
            offset += name_len

            # Write 8 stat bytes
            offset = self._write_player_stats(player, offset)
            written += 1

        # Write terminator (0x02 0x00 = empty string)
        if offset + 2 <= end:
            self.data[offset] = 0x02
            self.data[offset + 1] = 0x00
            offset += 2

        # Zero-fill any remaining space in the region
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
        """Write player count + line assignments into header.

        Updates byte 17 (player count nibble) and bytes 19-74
        (8 lines x 7 slots) so the game's line display matches
        the G+F+D player order we wrote.

        `num_forwards` and `num_defensemen` are the counts that reached the
        image, not the counts the selection asked for. `header_counts` is what
        turns one into the other and carries the argument; callers that pass the
        requested triple here will write a line table that indexes records the
        writer never wrote.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return False

        file_off = self.reader._read_team_pointer(team_index)
        if file_off is None:
            return False

        # -- Write player count byte (byte 17) --------
        pc_off = file_off + PLAYER_COUNT_OFFSET
        if pc_off >= len(self.data):
            return False
        nf = min(15, max(0, num_forwards))
        nd = min(15, max(0, num_defensemen))
        self.data[pc_off] = (nf << 4) | nd

        # -- Build line assignments ---------------------
        # Player indices:
        #   G:  0, 1
        #   F:  2  .. 2+nf-1    (LW,C,RW per line)
        #   D:  2+nf .. 2+nf+nd-1
        g1, g2 = 0, min(1, 1)
        f_base = HEADER_GOALIE_SLOTS
        d_base = HEADER_GOALIE_SLOTS + nf

        # Clamp helper
        def fi(i: int) -> int:
            return min(f_base + i, f_base + nf - 1)

        def di(i: int) -> int:
            return min(d_base + i, d_base + nd - 1)

        # Forward lines (groups of 3: LW, C, RW)
        # Defense pairs (groups of 2)
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

        # -- Write 56 bytes starting at byte 19 --------
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
        # SC1 - best line
        sc1 = [g1, di(0), di(1), fi(0), fi(1), fi(2), fi(0)]
        # SC2 - second line
        sc2 = [g1, di(2), di(3), fi(3), fi(4), fi(5), fi(3)]
        # CHK - checking / third line
        sc3 = [g1, di(4), di(5), fi(6), fi(7), fi(8), fi(6)]
        # PP1 - power play 1 (top scorers + top D)
        pp1 = [g1, di(0), di(1), fi(0), fi(1), fi(2), fi(3)]
        # PP2 - power play 2
        pp2 = [g1, di(2), di(3), fi(3), fi(4), fi(5), fi(6)]
        # PK1 - penalty kill 1 (checking fwds + top D)
        pk1 = [g1, di(0), di(1), fi(6), fi(7), fi(8), fi(6)]
        # PK2 - penalty kill 2
        pk2 = [g1, di(2), di(3), fi(3), fi(4), fi(5), fi(3)]
        # EA - extra attacker (backup G, pull for extra F)
        ea = [g2, di(0), di(1), fi(0), fi(1), fi(2), fi(3)]

        return [sc1, sc2, sc3, pp1, pp2, pk1, pk2, ea]

    def _write_player_stats(self, player: NHL94PlayerRecord, offset: int) -> int:
        """Write 8 stat bytes for a player. Returns the offset after them.

        INHERITED DEFECT, PRESERVED DELIBERATELY, and the reason is that in this
        package it cannot fire without something louder firing first.

        The shape is real: on the out-of-range branch this returns `offset`
        unchanged, which is indistinguishable from success, so
        `write_team_roster` counts the record and lays the next one down over
        the same bytes. Fixing that in isolation would change nothing, because
        of an arithmetic fact about the caller.

        `write_team_roster` only enters the loop body while
        `end - offset >= 13`, and a name is truncated to `end - offset - 12`, so
        the offset this is called with is at most `end - 10` and
        `offset + STATS_SIZE` is at most `end - 2`. The branch above therefore
        requires `end > len(self.data) + 2` -- a region that runs past the end
        of the image. And every such region ends in `IndexError`: the zero-fill
        that closes `write_team_roster` runs `while offset < end` and walks off
        the image, and `patcher.patch` turns that into a `RomError` before
        anything reaches disk. Swept 18 610 region shapes that do reach this
        branch; every one raised, none returned.

        So the two candidate fixes both make things worse, not better:

          * returning a sentinel and breaking the loop leaves the zero-fill to
            raise anyway -- identical behaviour, one more branch;
          * clamping `end` to `len(self.data)` in the caller would stop the
            raise, and that is exactly the outcome `patcher.patch` argues
            against: it would write a truncated roster into a record chain the
            ROM itself says is corrupt, and report success.

        `tests/games/nhl94_snes/test_rom_writer.py` pins both halves -- that
        this branch returns the offset unchanged, and that reaching it through
        `write_team_roster` raises. If anyone ever does clamp `end`, the second
        of those fails and this branch becomes live for real.
        """
        if not self.data or offset + STATS_SIZE > len(self.data):
            return offset

        attrs = player.attributes

        # Byte 0: Jersey number (BCD)
        jersey = max(1, min(99, player.jersey_number))
        self.data[offset] = ((jersey // 10) << 4) | (jersey % 10)
        offset += 1

        # Byte 1: Weight class (0-14) | Agility (0-6)
        self.data[offset] = encode_weight_nibble(player.weight_class, attrs.agility)
        offset += 1

        # Byte 2: Speed (0-6) | Off. Awareness (0-6)
        self.data[offset] = encode_nibble(attrs.speed, attrs.off_awareness)
        offset += 1

        # Byte 3: Def. Awareness (0-6) | Shot Power (0-6)
        self.data[offset] = encode_nibble(attrs.def_awareness, attrs.shot_power)
        offset += 1

        # Byte 4: Checking (0-6) | Handedness (0=L, 1=R)
        self.data[offset] = encode_nibble(attrs.checking, player.handedness)
        offset += 1

        # Byte 5: Stick Handling (0-6) | Shot Accuracy (0-6)
        self.data[offset] = encode_nibble(attrs.stick_handling, attrs.shot_accuracy)
        offset += 1

        # Byte 6: Endurance (0-6) | Roughness (0-6)
        self.data[offset] = encode_nibble(attrs.endurance, attrs.roughness)
        offset += 1

        # Byte 7: Pass Accuracy (0-6) | Aggression (0-6)
        self.data[offset] = encode_nibble(attrs.pass_accuracy, attrs.aggression)
        offset += 1

        return offset

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
