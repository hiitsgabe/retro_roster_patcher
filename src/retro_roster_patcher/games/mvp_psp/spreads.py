"""The disc's own spread of each rating, which `patch` puts a player's rank onto.

A rating is written as the value at the player's rank (0-1) of the values the
disc's own players of his group hold in that column: the league's best base
stealer gets the stealing of the disc's best, its typical one the disc's
typical, so a patched roster keeps the game's distribution. A player the
mapper has no stat for gets his position's median. Groups are a player's
position (`pos5`), his kind (`B` batters, `SP`, `RP`), a pitcher's role and
throwing hand (`SP1` a left-handed starter), and in the split tables the side
a batter swings from (`R`, `L`). The MVP Legends (`ATTRIB_HIDDEN`) and the
`Default` template are left out.

The spreads are read from the disc being patched, not written down: its own
column ranges (stealing 0-15, speed 39-99, fastball 78-101 mph) come with them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from statistics import mean

from .models import (
    ATTRIB_BATS,
    ATTRIB_BODY_TYPE,
    ATTRIB_BONE_PROFILE,
    ATTRIB_FIRST_NAME,
    ATTRIB_HEIGHT,
    ATTRIB_HIDDEN,
    ATTRIB_PRIMARY_POS,
    ATTRIB_THROWS,
    ATTRIB_WEIGHT,
    LR_ZONES,
    NO_PITCH,
    PA_PITCH1_VELOCITY,
    PA_PITCH2_TYPE,
    PA_PITCH_CONTROL_OFFSET,
    PA_PITCH_DESC_OFFSET,
    PA_PITCH_MOVEMENT_OFFSET,
    PA_PITCH_SLOTS,
    PA_PITCH_STRIDE,
    PA_PITCH_VELOCITY_OFFSET,
    ZONE_COLD,
    ZONE_HOT,
)

#: (table, column, group): a column is a number, or for a derived figure a
#: name (`hot`, `cold` zones, `move6` a slider's movement).
Key = tuple[str, int | str, str]
Table = Mapping[str, Mapping[int, str]]

#: A value shared by fewer of the disc's players than this is one player's own
#: (Ichiro's stance, Schilling's delivery) and is never handed to another.
SHARED = 5

#: `attrib` primary positions: 0 starters, 10 relievers.
STARTER, RELIEVER = 0, 10


def _int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def groups(attrib_row: Mapping[int, str]) -> list[str]:
    """The groups a disc player's values count in."""
    pos = _int(attrib_row.get(ATTRIB_PRIMARY_POS))
    if pos is None:
        return []
    kind = {STARTER: "SP", RELIEVER: "RP"}.get(pos, "B")
    out = [f"pos{pos}", kind, "all"]
    if kind != "B":
        out.append(f"{kind}{attrib_row.get(ATTRIB_THROWS, '0')}")
    return out


def batting_group(bats: int, lhp: bool) -> str:
    """The split-table group of a batter facing a left- (`lhp`) or right-hander."""
    if bats == 2:
        return "R" if lhp else "L"
    return "L" if bats == 1 else "R"


@dataclass
class Spreads:
    values: dict[Key, list[int]] = field(default_factory=dict)
    #: (height code, weight code, bone profile, body type) of each disc player.
    bodies: list[tuple[int, int, int, int]] = field(default_factory=list)

    @classmethod
    def of(cls, records: Mapping[str, Table], lhp_table: str, rhp_table: str) -> Spreads:
        """The spreads of a disc's parsed tables (`MVPPSPRomReader.records`)."""
        values: dict[Key, list[int]] = defaultdict(list)
        bodies = []
        attrib = records.get("attrib", {})
        pitchattrib = records.get("pitchattrib", {})
        for pid, row in attrib.items():
            if row.get(ATTRIB_HIDDEN) == "1" or row.get(ATTRIB_FIRST_NAME) == "Default":
                continue
            own = groups(row)
            for col, value in row.items():
                if (v := _int(value)) is not None:
                    for g in own:
                        values["attrib", col, g].append(v)
            build = [_int(row.get(c)) for c in (ATTRIB_HEIGHT, ATTRIB_WEIGHT)]
            build += [_int(row.get(c)) for c in (ATTRIB_BONE_PROFILE, ATTRIB_BODY_TYPE)]
            if None not in build:
                bodies.append(tuple(build))  # type: ignore[arg-type]
            bats = _int(row.get(ATTRIB_BATS)) or 0
            for table, lhp in ((lhp_table, True), (rhp_table, False)):
                split = records.get(table, {}).get(pid)
                if not split:
                    continue
                side = [*own, batting_group(bats, lhp)] if "B" in own else own
                zones = [split.get(c) for c in LR_ZONES]
                for col, value in split.items():
                    if (v := _int(value)) is not None:
                        for g in side:
                            values[table, col, g].append(v)
                if all(z is not None for z in zones):
                    for g in side:
                        values[table, "hot", g].append(zones.count(str(ZONE_HOT)))
                        values[table, "cold", g].append(zones.count(str(ZONE_COLD)))
            pitches = pitchattrib.get(pid)
            if pitches:
                for col, value in pitches.items():
                    if (v := _int(value)) is not None:
                        for g in own:
                            values["pitchattrib", col, g].append(v)
                fastball = _int(pitches.get(PA_PITCH1_VELOCITY))
                for slot in range(PA_PITCH_SLOTS):
                    base = PA_PITCH2_TYPE + slot * PA_PITCH_STRIDE
                    kind = pitches.get(base, NO_PITCH)
                    if kind == NO_PITCH or _int(kind) is None:
                        continue
                    figures = {
                        "move": _int(pitches.get(base + PA_PITCH_MOVEMENT_OFFSET)),
                        "desc": _int(pitches.get(base + PA_PITCH_DESC_OFFSET)),
                        "ctrl": _int(pitches.get(base + PA_PITCH_CONTROL_OFFSET)),
                        "drop": None,
                    }
                    velocity = _int(pitches.get(base + PA_PITCH_VELOCITY_OFFSET))
                    if velocity is not None and fastball is not None:
                        figures["drop"] = fastball - velocity
                    for name, v in figures.items():
                        if v is not None:
                            values["pitchattrib", f"{name}{kind}", "all"].append(v)
        return cls({k: sorted(v) for k, v in values.items()}, bodies)

    def at(self, table: str, column: int | str, group: str, share: float) -> int | None:
        """The value at rank `share` of the group's spread; None when the disc
        has no such column."""
        pool = self.values.get((table, column, group))
        if not pool:
            return None
        return pool[min(int(share * len(pool)), len(pool) - 1)]

    def mean(self, table: str, column: int | str, group: str) -> float | None:
        pool = self.values.get((table, column, group))
        return mean(pool) if pool else None

    def common(self, table: str, column: int | str, group: str) -> tuple[list[int], list[int]]:
        """The values `SHARED` or more of the group hold, commonest first, and their counts."""
        counts = Counter(self.values.get((table, column, group), [])).most_common()
        shared = [(v, n) for v, n in counts if n >= SHARED]
        return [v for v, _ in shared], [n for _, n in shared]

    def build(self, height: int, weight: int) -> tuple[int, int] | None:
        """The bone profile and body type of the disc's players nearest this
        height and weight (column codes): the profile whose players' mean
        build is closest, an inch counting as five pounds, and its commonest
        body type. None when the disc has no bodies."""
        by_profile: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
        for body in self.bodies:
            by_profile[body[2]].append(body)
        shared = {p: b for p, b in by_profile.items() if len(b) >= SHARED}
        if not shared:
            return None

        def distance(profile: int) -> float:
            bodies = shared[profile]
            dh = height - mean(b[0] for b in bodies)
            dw = weight - mean(b[1] for b in bodies)
            return (5 * dh) ** 2 + dw**2

        profile = min(sorted(shared), key=distance)
        body_type = Counter(b[3] for b in shared[profile]).most_common(1)[0][0]
        return profile, body_type
