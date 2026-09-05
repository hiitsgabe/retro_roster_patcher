"""ROM writer for NBA Live 95 patcher.

Writes player data to NBA Live 95 (Sega Genesis) ROM.
Player records are variable-length: 69 fixed bytes + variable name.
Records are packed adjacently — name must fit within the gap to
the next record.  Big-endian format (Motorola 68000).

References:
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

# Fixed portion of player record (before name)
FIXED_SIZE = OFF_NAME  # 0x45 = 69 bytes


def _encode_name_variable(last: str, first: str, max_bytes: int) -> bytes:
    """Encode player name to fit within max_bytes.

    Format: "Lastname\\0First" or "Lastname\\0F." if space is tight.
    Terminated by two consecutive null bytes.
    """
    last_bytes = last.encode("ascii", errors="replace")
    first_bytes = first.encode("ascii", errors="replace")

    # Need: last + \0 + first_initial + . + \0\0 = minimum
    # Or:   last + \0 + first + \0\0
    min_needed = len(last_bytes) + 1 + 2 + 2  # last + \0 + F. + \0\0

    if min_needed > max_bytes and len(last_bytes) > max_bytes - 5:
        last_bytes = last_bytes[: max(1, max_bytes - 5)]

    # Try full first name
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

    # Use first initial + period
    abbrev_len = len(last_bytes) + 1 + 2 + 2  # last + \0 + F. + \0\0
    if abbrev_len <= max_bytes:
        result = bytearray(abbrev_len)
        result[: len(last_bytes)] = last_bytes
        pos = len(last_bytes)
        result[pos] = 0
        pos += 1
        # DELIBERATE DIVERGENCE: upstream wrote `first_bytes[0] if first_bytes
        # else ord("A")`, and the fallback was dead code -- it invented the
        # initial `A.` for a player with no forename, and nothing could ever
        # reach it.
        #
        # Reaching this branch at all needs `full_len > max_bytes` above and
        # `abbrev_len <= max_bytes` here, so `full_len > abbrev_len`. Both are
        # computed from the same, possibly-truncated `last_bytes`:
        #
        #     full_len   = len(last_bytes) + len(first_bytes) + 3
        #     abbrev_len = len(last_bytes) + 5
        #
        # so the condition is `len(first_bytes) > 2`. The initial form is only
        # ever chosen for a forename of three characters or more -- at two it
        # ties with the full name and the full name has already been returned --
        # which makes `first_bytes[0]` safe by the same inequality that gets us
        # here, for every budget and every pair of names. Nothing about the
        # truncation above enters the argument.
        result[pos] = first_bytes[0]
        result[pos + 1] = ord(".")
        pos += 2
        result[pos] = 0
        result[pos + 1] = 0
        return bytes(result)

    # Last resort: just last name
    result = bytearray(min(len(last_bytes) + 2, max_bytes))
    # DELIBERATE DIVERGENCE: upstream indexed the left side by `len(last_bytes)`
    # and the right side by `len(result) - 2`. On a `bytearray` a slice
    # assignment whose sides differ in length *resizes* the buffer, so whenever
    # the surname was longer than the room for it the buffer shrank by the
    # difference and `result[-2]` below indexed off the front of a buffer that
    # no longer had two bytes in it. A budget of 2 raised `IndexError` out of a
    # function whose whole job is to fit a name into a budget.
    #
    # Both sides are now the same `keep`, which is what a fixed-layout encoder
    # means: fill what fits and leave the two-null terminator alone. Where
    # `len(last_bytes) <= keep` the two indices already agreed and nothing
    # moves, which is every budget of 3 or more -- this branch is only reached
    # with a surname truncated to one byte, or with no surname at all.
    #
    # Budgets of 0 and 1 still raise `IndexError` from `result[-2]`, and that is
    # not a residue: a name field shorter than its own two-byte terminator has
    # no encoding, and inventing one would mean returning bytes that overrun the
    # budget. `_compute_record_limits` floors every budget at 4 and
    # `write_player` defaults to 24, so no caller in this package can reach any
    # of the three.
    #
    # `max(0, ...)` changes no output and is not claimed to: at budgets 0 and 1
    # this branch is only ever reached with a surname of at most one byte, so
    # both sides of the assignment come out empty either way, and dropping the
    # guard is a declared equivalent mutant -- swept over 89 010 triples. It is
    # here because a negative slice bound counts from the other end rather than
    # meaning "no room", which is the same class of accident as the resize the
    # line above it replaces, and only the `min` two lines up keeps it out of
    # reach.
    keep = max(0, len(result) - 2)
    result[:keep] = last_bytes[:keep]
    result[-2] = 0
    result[-1] = 0
    return bytes(result)


class NBALive95RomWriter:
    """Writes player data to NBA Live 95 ROM.

    Player records are variable-length at pointer-referenced offsets.
    Only the fixed portion (69 bytes) and name are written, with the
    name sized to fit the available gap to the next record.
    """

    def __init__(self, rom_path: str, output_path: str):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = NBALive95RomReader(rom_path)
        self._record_limits: dict = {}  # (team, slot) -> max bytes for name

    def load(self) -> bool:
        """Load ROM data and precompute record size limits."""
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
        """Compute how many bytes each player's name can occupy.

        Records are packed: the gap between a player's offset and
        the next player's offset determines the max record size.
        """
        if not self.data:
            return

        for team_idx in range(TEAM_COUNT):
            team_addr = TEAM_ROSTER_ADDRESSES[team_idx]
            if team_addr == 0:
                continue

            # Collect all player offsets for this team, sorted
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
                    # Last player: use original name length + fixed
                    gap = self._original_record_size(ptr)

                max_name = gap - FIXED_SIZE
                self._record_limits[(team_idx, slot)] = max(4, max_name)

    def _original_record_size(self, ptr: int) -> int:
        """Get original record size by finding end of name (two nulls)."""
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
        """Apply checksum bypass to disable game's internal verification."""
        if not self.data:
            return
        if CHECKSUM_BYPASS_OFFSET + len(CHECKSUM_BYPASS_BYTES) <= len(self.data):
            self.data[
                CHECKSUM_BYPASS_OFFSET : CHECKSUM_BYPASS_OFFSET + len(CHECKSUM_BYPASS_BYTES)
            ] = CHECKSUM_BYPASS_BYTES

    def write_player(
        self, team_index: int, player_slot: int, player: NBALive95PlayerRecord
    ) -> bool:
        """Write a player record to ROM, respecting variable-length layout."""
        if not self.data or team_index >= TEAM_COUNT:
            return False
        if player_slot >= PLAYERS_PER_TEAM:
            return False

        off = self.reader._get_player_offset(team_index, player_slot)
        if off == 0 or off + FIXED_SIZE > len(self.data):
            return False

        d = self.data

        # Fixed fields (0x00-0x44)
        d[off + OFF_JERSEY] = max(0, min(99, player.jersey))
        d[off + OFF_POSITION] = max(0, min(4, player.position))
        d[off + OFF_HEIGHT] = max(0, min(255, player.height_inches))
        d[off + OFF_WEIGHT] = max(0, min(255, player.weight_lbs - 100))
        d[off + OFF_EXPERIENCE] = max(0, min(255, player.experience))

        # UPSTREAM BEHAVIOUR, KNOWN WRONG, PRESERVED DELIBERATELY: both bytes are
        # written for every player, and nothing in this package supplies either.
        # `stat_mapper.map_player` builds the record without them, so both arrive
        # here as `NBALive95PlayerRecord`'s default of 0 for all 324 patched
        # players, and every patched player therefore gets the same skin tone and
        # the same hair laid over whatever variety the 1994 image shipped with.
        # 0 is not a "not supplied" code in either field -- it is a real tone out
        # of 4 and a real style out of 39 -- so this asserts an appearance from
        # nothing. ESPN publishes neither attribute and no provider here can fill
        # them. This port skipped the two writes for a while, on the argument
        # that the image's own per-player byte is strictly more information than
        # one constant.
        #
        # It writes them the upstream way again because skipping them is a record
        # layout no released build of this patcher ever produced. Nothing in this
        # repository has been validated against a real dump, and a 93-byte record
        # that differs from the one the game has actually been fed is a crash
        # risk on hardware that outweighs a uniform-looking roster. Fidelity to
        # the original beats correctness of the data. Do not re-add the `> 0`
        # guards.
        #
        # The `max(0, ...)` halves are upstream's and are kept with them: inside
        # the old `> 0` guard they were dead, and outside it they are what stops
        # a negative field reaching the buffer as a wrapped byte.
        d[off + OFF_SKIN] = max(0, min(3, player.skin_color))
        d[off + OFF_HAIR] = max(0, min(0x26, player.hair_style))

        # Season stats (17 x 2-byte BE)
        #
        # DELIBERATE DIVERGENCE: the clamp is new. Upstream packed this value
        # straight into a `>H`, alone among this method's eight numeric fields
        # -- jersey, position, height, weight, experience, skin, hair and the 16
        # ratings are all clamped -- so a stat above 65 535 or below 0 came out
        # of a `bool`-returning writer as a `struct.error`, which is outside this
        # library's exception hierarchy and reaches `patch`'s caller uncaught.
        #
        # Not reachable today: `stat_mapper.map_player` supplies 17 zeros and
        # says why. It becomes reachable the moment anything starts supplying
        # real numbers, and a season point total is a plausible thing to overrun
        # a 16-bit field with. Clamping now rather than later is what keeps that
        # future change from being two problems at once; it costs nothing while
        # the values are zeros, and it is the same answer the other eight fields
        # already give to an out-of-range number.
        for i in range(STAT_COUNT):
            stat_val = player.season_stats[i] if i < len(player.season_stats) else 0
            struct.pack_into(">H", d, off + OFF_STATS + i * 2, max(0, min(0xFFFF, stat_val)))

        d[off + OFF_UNKNOWN2] = 0x00

        # Ratings (16 x 1 byte, 0-99 scale)
        for i in range(RATING_COUNT):
            rating = player.ratings[i] if i < len(player.ratings) else 50
            d[off + OFF_RATINGS + i] = max(0, min(99, rating))

        # Preserve unknown bytes 0x3B-0x44 (don't zero them)

        # Name: variable length, must fit in available gap
        max_name = self._record_limits.get((team_index, player_slot), 24)
        name_bytes = _encode_name_variable(player.name_last, player.name_first, max_name)
        d[off + OFF_NAME : off + OFF_NAME + len(name_bytes)] = name_bytes

        return True

    def write_team_roster(self, team_index: int, players: list[NBALive95PlayerRecord]) -> int:
        """Write all players for a team.

        Returns number of players written.
        """
        if not self.data or team_index >= TEAM_COUNT:
            return -1

        written = 0
        for slot, player in enumerate(players[:PLAYERS_PER_TEAM]):
            if self.write_player(team_index, slot, player):
                written += 1

        return written

    def _fix_checksum(self) -> None:
        """Recalculate and update the Genesis ROM header checksum.

        The checksum at offset 0x18E is the 16-bit sum of all
        big-endian words from 0x200 to end of ROM.
        """
        if not self.data or len(self.data) < 0x200:
            return
        total = 0
        for i in range(0x200, len(self.data), 2):
            if i + 1 < len(self.data):
                total += struct.unpack_from(">H", self.data, i)[0]
        struct.pack_into(">H", self.data, 0x18E, total & 0xFFFF)

    def finalize(self) -> bool:
        """Write the modified ROM to output path."""
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
