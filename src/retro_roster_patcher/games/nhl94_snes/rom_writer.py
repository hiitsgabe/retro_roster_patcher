"""ROM writer for NHL94 SNES patcher.

  - https://github.com/clandrew/nhl94e
  - https://cml-a.com/content/2020/11/23/names-and-stats-in-nhl-94/

Team data lives in bank $9C. The pointer table at $9C:A5E7 holds one 16-bit
address per team, and every reader in the game -- some 300 sites -- supplies
bank $9C itself, so no team block can leave that bank. A block is
[header][player records][0x0002 terminator][4 strings: city, abbreviation,
nickname, arena], and everything in it is found by 16-bit offsets from its
start: word 0 to the first record, word 4 to the strings, the rest to parts of
the header itself.

Real names do not fit the 1993 blocks, so `write_rosters` rebuilds all 28 in
the room bank $9C has for them -- where the blocks are, plus the 0xFF padding
that ends the bank -- and repoints the table. Every name keeps up to the 19
characters the cartridge's longest has; only when the bank is full do the
longest lose their first names to an initial ("C. McDavid"), and a surname is
never cut. The strings move with their block, untouched.

The header checksum is left to the caller: the SNES does not verify it and this
game does not read it.
"""

from __future__ import annotations

import os
import struct
from dataclasses import replace

from ...core.errors import RomError
from .models import (
    TEAM_COUNT,
    NHL94GoalieAttributes,
    NHL94PlayerRecord,
)
from .rom_reader import (
    PLAYER_COUNT_OFFSET,
    STATS_SIZE,
    NHL94SNESRomReader,
)

# Team-data header, 85 bytes on the retail cartridge: byte 14 the score
# simulator's attack/leakiness nibbles, 15 a power-play bonus, 16 a home/away
# bonus, 17 the F|D counts, 18 the team overall (Matchups), bytes 19-20 one
# nibble per goalie, and bytes 21-84 the line table, 8 records of 8 bytes.
STRENGTH_OFFSET = 14
BONUS_OFFSETS = (15, 16)
OVERALL_OFFSET = 18
GOALIE_OFFSET = 19
LINE_ASSIGN_OFFSET = 21
LINE_SLOTS = 8  # G, LD, RD, LW, C, RW, EA, 0x00
LINE_COUNT = 8  # Best, SC1, SC2, CHK, PP1, PP2, PK1, PK2

# (forward trio, defence pair) behind each record, in the cartridge's order.
# Best plays when line changes are off and Scoring 1 leads them when they are
# on, so both are the first trio and the first pair.
LINE_UNITS = [(0, 0), (0, 0), (1, 1), (2, 2), (0, 0), (1, 1), (2, 0), (1, 1)]

# The line table counts forwards from record 3, so it always assumes two goalie
# records precede them.
HEADER_GOALIE_SLOTS = 2

#: The cartridge's longest name ("Vladimir Konstantov").
MAX_NAME_LENGTH = 19

#: Bank $9C in a headerless image, and the block offset word for the strings.
BANK_FILE_OFFSET = 0xE0000
BANK_SIZE = 0x8000
STRINGS_OFFSET_WORD = 4
TEAM_STRINGS = 4

#: The retail hot/cold sign bug at $9F:D232: `LDA $1A12,Y / LSR / LSR / BIT
#: #$00 / BEQ / ORA #$00` sign-extends a per-game modifier with zero masks, so a
#: negative one maxes the attribute at 15. `BIT #$20 / ORA #$C0` extends it.
#: File offset of the routine (headerless) and its two operands either way.
HOT_COLD_CODE = 0xFD232
HOT_COLD_RETAIL = (0x00, 0x00)
HOT_COLD_FIXED = (0x20, 0xC0)


def _hot_cold_code(operands: tuple[int, int]) -> bytes:
    return bytes.fromhex("e220b9121a4a4a89{:02x}f00209{:02x}18".format(*operands))


# The game's own overall rating weights ($9F:9AD8), by nibble: nibble n is the
# high half of stat byte n // 2 when n is even, the low half when odd.
SKATER_WEIGHTS = {3: 2, 4: 3, 5: 3, 6: 2, 7: 1, 8: 2, 10: 3, 11: 2, 12: 1, 14: 1}
GOALIE_WEIGHTS = {3: 9, 6: 9, 7: 9, 12: 2, 13: 2, 14: 2, 15: 2}


def header_counts(written: int, num_forwards: int, num_defensemen: int) -> tuple[int, int]:
    """The `(forwards, defencemen)` a header may claim for `written` records.

    The line table indexes players by absolute position -- forwards after the
    `HEADER_GOALIE_SLOTS` goalies, defencemen after the forwards -- so a
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


def abbreviate(name: str) -> str:
    """'Connor McDavid' -> 'C. McDavid'. A one-word name, or one whose first
    word is already an initial, comes back as it was."""
    first, sep, rest = name.partition(" ")
    if not sep or len(first) <= 2:
        return name
    return f"{first[0]}. {rest}"


def player_stats(player: NHL94PlayerRecord) -> bytes:
    """A player's 8 stat bytes.

    Byte 0: Jersey number (BCD)
    Byte 1: Weight class (0-14) | Agility
    Byte 2: Speed | Off. Awareness (a goalie: Def. Awareness again, as retail)
    Byte 3: Def. Awareness | Shot Power (a goalie: Puck Control)
    Byte 4: Checking (a goalie: 0) | Handedness (0=L, 1=R; a goalie's glove)
    Byte 5: Stick Handling | Shot Accuracy (a goalie: 0 | 0)
    Byte 6: Endurance | Roughness (a goalie: Stick Right | Stick Left)
    Byte 7: Pass Accuracy | Aggression (a goalie: Glove Right | Glove Left)
    """
    a = player.attributes
    jersey = max(1, min(99, player.jersey_number))
    hand = max(0, min(1, player.handedness))
    if isinstance(a, NHL94GoalieAttributes):
        body = [
            encode_weight_nibble(player.weight_class, a.agility),
            encode_nibble(a.speed, a.def_awareness),
            encode_nibble(a.def_awareness, a.puck_control),
            hand,
            0,
            encode_nibble(a.stick_right, a.stick_left),
            encode_nibble(a.glove_right, a.glove_left),
        ]
    else:
        body = [
            encode_weight_nibble(player.weight_class, a.agility),
            encode_nibble(a.speed, a.off_awareness),
            encode_nibble(a.def_awareness, a.shot_power),
            encode_nibble(a.checking, hand),
            encode_nibble(a.stick_handling, a.shot_accuracy),
            encode_nibble(a.endurance, a.roughness),
            encode_nibble(a.pass_accuracy, a.aggression),
        ]
    return bytes([(jersey // 10) << 4 | jersey % 10, *body])


def game_overall(stats: bytes, goalie: bool) -> int:
    """The overall the Team Roster shows for these stat bytes, before the
    per-game modifiers: the weighted nibbles (a goalie's halved), capped at 100
    and squashed below 50 to v / 2 + 25."""
    weights = GOALIE_WEIGHTS if goalie else SKATER_WEIGHTS
    nibble = [n for b in stats for n in (b >> 4, b & 15)]
    total = sum(w * nibble[n] for n, w in weights.items())
    total = min(100, total // 2 if goalie else total)
    return total if total >= 50 else total // 2 + 25


def encode_records(players: list[NHL94PlayerRecord]) -> bytes:
    """The records and their terminator: per player a little-endian length word
    (counting itself and the name), the ASCII name and the 8 stat bytes.

    Upstream's behaviour, known wrong, preserved: an empty name writes a length
    word of 2, which the readers treat as the end-of-roster terminator.
    """
    out = bytearray()
    for player in players:
        name = player.name.encode("ascii", errors="replace")
        out += struct.pack("<H", len(name) + 2) + name + player_stats(player)
    return bytes(out + b"\x02\x00")


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _pack(sizes: dict[int, int], pool: list[tuple[int, int]]) -> dict[int, int] | None:
    """Where each block goes: largest first, into the first room (by address)
    with space, from the room's top down -- so what is left free is the bottom
    of each room, the start of the bank's padding among it. None if one does
    not fit."""
    rooms = [list(room) for room in pool]
    where: dict[int, int] = {}
    for slot in sorted(sizes, key=lambda s: (-sizes[s], s)):
        for room in rooms:
            if room[1] - room[0] >= sizes[slot]:
                room[1] -= sizes[slot]
                where[slot] = room[1]
                break
        else:
            return None
    return where


class NHL94SNESRomWriter:
    def __init__(self, rom_path: str, output_path: str):
        self.rom_path = rom_path
        self.output_path = output_path
        self.data: bytearray | None = None
        self.reader = NHL94SNESRomReader(rom_path)

    def load(self) -> bool:
        if not self.reader.load() or self.reader.data is None:
            return False
        # One buffer: once `write_rosters` moves the blocks, the reader's
        # pointers must be the new ones.
        self.data = self.reader.data
        return True

    # -- bank $9C ----------------------------------------------------------

    def _bank(self) -> tuple[int, int]:
        start = self.reader.header_offset + BANK_FILE_OFFSET
        return start, start + BANK_SIZE

    def _u16(self, offset: int) -> int:
        assert self.data is not None
        lo, hi = self._bank()
        if not lo <= offset <= hi - 2:
            raise RomError(f"Corrupt team data: {offset:#x} is outside bank $9C")
        return self.data[offset] | self.data[offset + 1] << 8

    def _follow(self, base: int, offset: int) -> int:
        """`base` plus a block offset, wrapped inside bank $9C as the game's
        16-bit add wraps it."""
        lo, _hi = self._bank()
        address = (0x8000 + base - lo + offset) & 0xFFFF
        if address < 0x8000:
            raise RomError(f"Corrupt team data: a block offset leaves bank $9C at {base:#x}")
        return lo + address - 0x8000

    def _block(self, slot: int) -> tuple[bytes, bytes, bytes, list[tuple[int, int]]]:
        """A block's header, records (with terminator) and strings, and the file
        spans they occupy."""
        assert self.data is not None
        base = self.reader._read_team_pointer(slot)
        if base is None:
            raise RomError(f"Corrupt team data: no pointer for slot {slot}")
        header_size = self._u16(base)
        if header_size < LINE_ASSIGN_OFFSET + LINE_COUNT * LINE_SLOTS:
            raise RomError(f"Corrupt team data: slot {slot}'s header is {header_size} bytes")
        records = end = self._follow(base, header_size)
        while (length := self._u16(end)) >= 3:
            end += length + STATS_SIZE
        end += 2
        strings = tail = self._follow(base, self._u16(base + STRINGS_OFFSET_WORD))
        for _ in range(TEAM_STRINGS):
            length = self._u16(tail)
            if length < 2:
                raise RomError(f"Corrupt team data: slot {slot}'s strings at {strings:#x}")
            tail += length
        if end > self._bank()[1] or tail > self._bank()[1]:
            raise RomError(f"Corrupt team data: slot {slot}'s block runs out of bank $9C")
        d = self.data
        spans = [(base, base + header_size), (records, end), (strings, tail)]
        return (
            bytes(d[base : base + header_size]),
            bytes(d[records:end]),
            bytes(d[strings:tail]),
            spans,
        )

    def _room(self, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """The bytes the blocks may use: their own, the 0xFF runs touching them
        (what a previous patch left free) and the 0xFF padding ending the bank."""
        assert self.data is not None
        d = self.data
        lo, hi = self._bank()
        tail = hi
        while tail > lo and d[tail - 1] == 0xFF:
            tail -= 1
        rooms = []
        for start, end in _merge(spans):
            while start > lo and d[start - 1] == 0xFF:
                start -= 1
            while end < hi and d[end] == 0xFF:
                end += 1
            rooms.append((start, end))
        return _merge(rooms + ([(tail, hi)] if tail < hi else []))

    def write_rosters(self, rosters: dict[int, list[NHL94PlayerRecord]]) -> set[int]:
        """Write each slot's players and rebuild the team blocks around them;
        returns the slots written.

        A block that does not parse stays where it is, unwritten, and its bytes
        are not reused. Raises `RomError` when even with every first name
        abbreviated the blocks do not fit bank $9C.
        """
        if not self.data:
            raise RomError("No ROM loaded")
        if not rosters:
            return set()
        blocks = {}
        for slot in range(TEAM_COUNT):
            try:
                blocks[slot] = self._block(slot)
            except RomError:
                continue
        pool = self._room([span for block in blocks.values() for span in block[3]])
        players = {slot: list(team) for slot, team in rosters.items() if slot in blocks}
        records = {slot: encode_records(team) for slot, team in players.items()}

        def size(slot: int) -> int:
            header, old, strings, _ = blocks[slot]
            return len(header) + len(records.get(slot, old)) + len(strings)

        sizes = {slot: size(slot) for slot in blocks}
        while (where := _pack(sizes, pool)) is None:
            # The longest name that can still lose a first name does.
            candidates = [
                (len(p.name), slot, i)
                for slot, team in players.items()
                for i, p in enumerate(team)
                if abbreviate(p.name) != p.name
            ]
            if not candidates:
                room = sum(end - start for start, end in pool)
                raise RomError(
                    f"The rosters need {sum(sizes.values())} bytes of team data; "
                    f"bank $9C has {room}"
                )
            _, slot, i = max(candidates)
            team = players[slot]
            team[i] = replace(team[i], name=abbreviate(team[i].name))
            records[slot] = encode_records(team)
            sizes[slot] = size(slot)

        d = self.data
        lo, _hi = self._bank()
        for start, end in pool:
            d[start:end] = b"\xff" * (end - start)
        table = self.reader._ptr_table_offset()
        for slot, at in where.items():
            header, old, strings, _ = blocks[slot]
            body = records.get(slot, old)
            block = bytearray(header + body + strings)
            struct.pack_into("<H", block, STRINGS_OFFSET_WORD, len(header) + len(body))
            d[at : at + len(block)] = block
            struct.pack_into("<H", d, table + slot * 4, 0x8000 + at - lo)
        return set(players)

    # -- header --------------------------------------------------------------

    def _base(self, team_index: int) -> int | None:
        if not self.data or not 0 <= team_index < TEAM_COUNT:
            return None
        return self.reader._read_team_pointer(team_index)

    def write_team_header(
        self,
        team_index: int,
        num_forwards: int,
        num_defensemen: int,
    ) -> bool:
        """Update byte 17 (the player-count nibbles), bytes 19-20 (the goalie
        nibbles) and bytes 21-84 (the line table) for a roster written as G, F, D
        blocks.

        The counts must be the ones that reached the image, not the ones the
        selection asked for; `header_counts` turns one into the other.
        """
        file_off = self._base(team_index)
        if file_off is None or self.data is None:
            return False

        pc_off = file_off + PLAYER_COUNT_OFFSET
        if pc_off >= len(self.data):
            return False
        nf = min(15, max(0, num_forwards))
        nd = min(15, max(0, num_defensemen))
        self.data[pc_off] = (nf << 4) | nd

        la_off = file_off + LINE_ASSIGN_OFFSET
        if la_off + LINE_COUNT * LINE_SLOTS > len(self.data):
            return False

        # One nonzero nibble per goalie, high first: the game counts its goalies
        # from them ($9E:D300) and nothing else reads them -- not the starter,
        # which is always G1. Two are written; the cartridge's values are kept
        # (its two largest), one with fewer gets 12 and 5.
        g = file_off + GOALIE_OFFSET
        w = sorted((n for b in self.data[g : g + 2] for n in (b >> 4, b & 15) if n), reverse=True)
        self.data[g] = (w[0] if w else 12) << 4 | (w[1] if len(w) > 1 else 5)
        self.data[g + 1] = 0

        for line_bytes in self._build_lines(nf, nd):
            for b in line_bytes:
                self.data[la_off] = b & 0xFF
                la_off += 1

        return True

    def write_team_ratings(
        self, team_index: int, players: list[NHL94PlayerRecord], strength: int | None
    ) -> bool:
        """Bytes 14-16 and 18, after `write_team_header`.

        14 is `strength` (None keeps the cartridge's); 15 (power-play bonus) and
        16 (home/away bonus) are 1993 team traits and go to 0; 18, the team
        overall on Matchups, is the mean overall of line record 0's six minus 2,
        which is how the cartridge's own fit (each of its 28 within 4).
        """
        base = self._base(team_index)
        if base is None or self.data is None:
            return False
        d = self.data
        if strength is not None:
            d[base + STRENGTH_OFFSET] = strength & 0x77
        for offset in BONUS_OFFSETS:
            d[base + offset] = 0
        line = d[base + LINE_ASSIGN_OFFSET : base + LINE_ASSIGN_OFFSET + 6]
        six = [
            game_overall(player_stats(players[i - 1]), position == 0)
            for position, i in enumerate(line)
            if 0 < i <= len(players)
        ]
        if six:
            d[base + OVERALL_OFFSET] = max(0, min(100, round(sum(six) / len(six)) - 2))
        return True

    @staticmethod
    def _build_lines(nf: int, nd: int) -> list[list[int]]:
        """The 8 records, `[G, LD, RD, LW, C, RW, EA, 0x00]`, with 1-based roster
        indices: goalies 1-2, forwards from 3 (LW, C, RW per trio), defencemen
        after them. An index past a block's end clamps to its last player.

        G is the starter in every record, as on the cartridge. EA, the extra
        attacker, is the next trio's left wing: the cartridge keeps it off the line.
        """
        f_base = HEADER_GOALIE_SLOTS + 1
        d_base = f_base + nf

        def fi(i: int) -> int:
            return min(f_base + i, f_base + nf - 1)

        def di(i: int) -> int:
            return min(d_base + i, d_base + nd - 1)

        lines = []
        for trio, pair in LINE_UNITS:
            lw, c, rw = (fi(3 * trio + k) for k in range(3))
            ea = fi(3 * ((trio + 1) % 3))
            lines.append([1, di(2 * pair), di(2 * pair + 1), lw, c, rw, ea, 0])
        return lines

    # -- code -------------------------------------------------------------------

    def set_hot_cold_fix(self, enabled: bool) -> bool:
        """Fix (or restore) the retail hot/cold sign bug. False, writing
        nothing, when the code there is not the routine this expects."""
        if not self.data:
            return False
        at = self.reader.header_offset + HOT_COLD_CODE
        code = _hot_cold_code(HOT_COLD_FIXED if enabled else HOT_COLD_RETAIL)
        if bytes(self.data[at : at + len(code)]) not in map(
            _hot_cold_code, (HOT_COLD_RETAIL, HOT_COLD_FIXED)
        ):
            return False
        self.data[at : at + len(code)] = code
        return True

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
