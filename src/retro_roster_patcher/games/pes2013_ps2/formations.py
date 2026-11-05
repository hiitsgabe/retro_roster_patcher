"""The team formation records of PES 2013 (PS2): `0_TEXT.AFS` entry 53.

The entry is a raw block whose payload is a part table of ten zlib blocks. Part 1 holds one
52-byte record per team, 197 of them: national row r is record r, club c is record 67 + c. The
record is the compact form of the editor's formation set: the game copies part 1 to RAM as it is
and builds the editor's 364-byte team record from it, the same set for Normal, Plan A and Plan B
(read off PCSX2's RAM on 2026-11-03):

    +0   10  role codes of places 1-10 (place 0, the keeper, is implicit)
    +10  10  x of places 1-10: along the pitch, 2 at the own goal line, about 43 for a forward
    +20  10  y of places 1-10: across it, 52 in the middle and low y the team's left
    +30  11  attack arrows of places 0-10, a bit per direction
    +41  11  defence of places 0-10: 0 two "stay back" marks, 1 one, 2 none

Squad place k (0 the keeper) stands in place k. The role codes are the editor's: 1-7 CB (1 left,
3 centre, 7 right, 4-5 sweepers), 8 LB, 9 RB, 10-14 DMF, 15 LWB, 16 RWB, 17-21 CMF, 22 LMF, 23
RMF, 24-28 AMF, 29 LWF, 30 RWF, 31-35 SS, 36-40 CF, and the editor keeps the codes 1-9 at x up to
15, 10-28 at 16 to 34 and 29-40 from 35 on. So a code's band is the line its place is on, and a
record lists its places defence first. Only the first 30 bytes change with a formation; the
arrows and the defence stay the team's.

Nothing here reads the disc: `RomReader.formations` and `RomWriter.write_formations` do.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ...core.errors import RomError
from ...sports.lineup import nearest_shape, parse_shape, seat_line
from .afs import Table, unwrap, wrap
from .models import FORMATION_PART, FORMATION_RECORD, FORMATION_TEAMS

#: The bytes a formation changes in a record: the role codes, x and y of places 1-10.
PRESET_BYTES = 30


def _preset(xy: str, roles: str) -> bytes:
    """A preset from the editor's row, as `FormPanel.formData` lists it: the x and y of places
    1-10 in pairs, then the role codes of places 0-10 (the keeper's 0 first). The disc keeps the
    codes of places 1-10, then every x, then every y."""
    pairs, codes = bytes.fromhex(xy), bytes.fromhex(roles)
    return codes[1:] + pairs[0::2] + pairs[1::2]


#: The editor's 12 formations in its drop-down's order (a tie in `pick_preset` goes to the first),
#: each as the 30 disc bytes.
PRESETS: dict[str, bytes] = {
    "4-4-2": _preset(
        "09 3f 09 29 0c 57 0c 11 1a 4d 1a 3d 1a 2b 1a 1b 2b 42 2b 26",
        "00 07 01 09 08 14 15 11 12 28 24",
    ),
    "4-3-1-2": _preset(
        "09 3f 09 29 0c 57 0c 11 12 34 1a 46 1a 22 22 34 2b 42 2b 26",
        "00 07 01 09 08 0c 15 11 1a 28 24",
    ),
    "4-4-1-1": _preset(
        "09 3f 09 29 0c 57 0c 11 12 3d 12 2b 1d 50 1d 18 20 34 2b 34",
        "00 07 01 09 08 0e 0a 17 16 1a 26",
    ),
    "4-2-1-3": _preset(
        "09 3f 09 29 0c 57 0c 11 12 3d 12 2b 20 34 2b 48 2b 20 2b 34",
        "00 07 01 09 08 0e 0a 1a 1e 1d 26",
    ),
    "4-5-1": _preset(
        "09 3f 09 29 0c 57 0c 11 12 34 1d 3d 1d 2b 1f 50 1f 18 2b 34",
        "00 07 01 09 08 0c 15 11 17 16 26",
    ),
    "4-1-2-3": _preset(
        "09 3f 09 29 0c 57 0c 11 12 34 20 46 20 22 2b 48 2b 20 2b 34",
        "00 07 01 09 08 0c 1c 18 1e 1d 26",
    ),
    "4-3-3": _preset(
        "09 3f 09 29 0c 57 0c 11 1a 4d 1a 34 1a 1b 2b 48 2b 20 2b 34",
        "00 07 01 09 08 15 13 11 1e 1d 26",
    ),
    "4-3-2-1": _preset(
        "09 3f 09 29 0c 57 0c 11 16 4d 16 34 16 1b 22 46 22 22 2b 34",
        "00 07 01 09 08 15 13 11 1c 18 26",
    ),
    "3-4-1-2": _preset(
        "09 48 09 34 09 20 12 3d 12 2b 18 50 18 18 20 34 2b 42 2b 26",
        "00 07 03 01 0e 0a 10 0f 1a 28 24",
    ),
    "3-3-2-2": _preset(
        "09 48 09 34 09 20 12 34 18 50 18 18 20 3d 20 2b 2b 42 2b 26",
        "00 07 03 01 0c 10 0f 1c 18 28 24",
    ),
    "3-4-3": _preset(
        "09 48 09 34 09 20 18 50 18 18 16 3d 16 2b 2b 48 2b 20 2b 34",
        "00 07 03 01 10 0f 15 11 1e 1d 26",
    ),
    "5-4-1": _preset(
        "09 48 09 34 09 20 0c 57 0c 11 12 3d 12 2b 1f 50 1f 18 2b 34",
        "00 07 03 01 09 08 0e 0a 17 16 26",
    ),
}
#: The three-line shapes the presets cover, which is what the patcher offers (`extra["formation"]`
#: stays three lines; the presets' four-part names never leave this module).
FORMATIONS = ("4-4-2", "4-3-3", "4-5-1", "3-5-2", "3-4-3", "5-4-1")

#: The role each code reads back as, codes 0-40 (the keeper, then the editor's table above).
_ROLE_OF_CODE = (
    ("GK",)
    + ("CB",) * 7
    + ("LB", "RB")
    + ("CDM",) * 5
    + ("LWB", "RWB")
    + ("CM",) * 5
    + ("LM", "RM")
    + ("CAM",) * 5
    + ("LW", "RW")
    + ("ST",) * 10
)

#: A place is on the team's left below this y and on its right above `RIGHT_ABOVE`, central between
#: (the disc's records put a wide place at y 7-37 or 71-101 and a central one at 31-77).
LEFT_BELOW = 45
RIGHT_ABOVE = 59


def lines(record: bytes) -> tuple[int, int, int]:
    """How many of places 1-10 are defenders, midfielders and forwards, by the code bands 1-9,
    10-28 and 29-40. `record` is a preset or a whole record: only its first 10 bytes are read."""
    codes = record[:10]
    defence = sum(1 <= c <= 9 for c in codes)
    attack = sum(c >= 29 for c in codes)
    return defence, len(codes) - defence - attack, attack


def place_roles(record: bytes) -> list[str]:
    """The role of each of places 1-10 (the keeper's place has no code and is always a GK)."""
    return [_ROLE_OF_CODE[c] for c in record[:10]]


def pick_preset(shape: str, roles: Sequence[str]) -> str:
    """The name of the preset for a team that plays `shape` with these outfield `roles`.

    Of the presets whose lines are the shape of `FORMATIONS` nearest to `shape` (four lines are
    read as three, `nearest_shape`), the one whose place roles share most with `roles` (the
    multiset they have in common). A tie goes to the editor's order, so a team with no roles gets
    the first of its shape."""
    want = parse_shape(nearest_shape(shape, FORMATIONS))
    have = Counter(roles)
    candidates = [name for name, preset in PRESETS.items() if lines(preset) == want]
    return max(
        candidates, key=lambda name: sum((Counter(place_roles(PRESETS[name])) & have).values())
    )


def _side(y: int) -> str:
    return "L" if y < LEFT_BELOW else "R" if y > RIGHT_ABOVE else "C"


def _own(places: list[str], role: str) -> int:
    """Where in a line's `places` a player of `role` has a place of exactly his role: its index,
    or past the end when the line has none."""
    return places.index(role) if role in places else len(places)


def seats(roles: Sequence[str], record: bytes) -> list[int]:
    """The place (0-9, for places 1-10) each outfield player stands on: `seats[i]` for the i-th of
    `roles`, which are cut into the record's lines by index (the first `lines(record)[0]` are the
    defence, and so on; a short list leaves the last places of its lines empty).

    Each line is seated on its places by `seat_line`: a place's side comes from its y and its depth
    from its x. A player whose role has a place in the line of exactly that role is offered to
    `seat_line` before the others, in the order of those places, so where side and depth tie
    (a right back and a centre back are both on the right and both deep) he keeps his own place,
    and where they do not they decide."""
    xs, ys = record[10:20], record[20:30]
    names = place_roles(record)
    out: list[int] = []
    first = 0
    for count in lines(record):
        at = range(first, min(first + count, len(roles)))
        have = [roles[k] for k in at]
        own = names[first : first + len(have)]
        order = sorted(range(len(have)), key=lambda i: _own(own, have[i]))
        sat = seat_line([have[i] for i in order], [(_side(ys[k]), xs[k]) for k in at])
        placed = dict(zip(order, sat, strict=True))
        out += [first + placed[i] for i in range(len(have))]
        first += count
    return out


@dataclass
class FormationTable:
    """Entry 53 as parts, of which only part 1 (one record per team) is read or changed."""

    header: bytes
    table: Table
    part_header: bytes
    leaf: bytearray

    @classmethod
    def parse(cls, block: bytes) -> FormationTable:
        header, payload = unwrap(block)
        table = Table.parse(payload)
        if len(table.parts) <= FORMATION_PART or not table.parts[FORMATION_PART]:
            raise RomError("Formation entry has no team part")
        part_header, leaf = unwrap(table.parts[FORMATION_PART])
        if len(leaf) < FORMATION_TEAMS * FORMATION_RECORD:
            raise RomError("Formation part is shorter than its team records")
        return cls(header, table, part_header, bytearray(leaf))

    def _at(self, team: int) -> int:
        if not 0 <= team < FORMATION_TEAMS:
            raise RomError(f"Team {team} has no formation record")
        return team * FORMATION_RECORD

    def get(self, team: int) -> bytes:
        """Team `team`'s 52 bytes: national row r is `r`, club c is `FIRST_CLUB_FORMATION + c`."""
        at = self._at(team)
        return bytes(self.leaf[at : at + FORMATION_RECORD])

    def set(self, team: int, preset: bytes) -> None:
        """Put a preset over the team's first 30 bytes, keeping its arrows and defence."""
        if len(preset) != PRESET_BYTES:
            raise ValueError(f"A preset is {PRESET_BYTES} bytes, not {len(preset)}")
        at = self._at(team)
        self.leaf[at : at + PRESET_BYTES] = preset

    def build(self) -> bytes:
        """The entry rebuilt: part 1 deflated again, every other part as it was."""
        self.table.parts[FORMATION_PART] = wrap(self.part_header, bytes(self.leaf))
        return wrap(self.header, self.table.build())
