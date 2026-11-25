"""A player's season, splits and career as the disc's statistics tables hold them.

Nine tables key on the player's id beside the four attribute ones. Each row is
his name (columns 0 and 1) then counts, in the order of the table's own header
line (`COLUMNS`, from column 2); the game works every rate it shows out of
them (the retail Manny Ramirez's .308 is his 175 hits over 663 plate
appearances less 82 walks, 6 hit by pitch and 7 sacrifice flies):

- `batstat` and `fieldstat`: last season's games, runs and steals, and his
  fielding line. Every player has a row; a pitcher's games are his
  appearances.
- `lrbatstat_lhp`, `lrbatstat_rhp`: last season's batting against left- and
  right-handed pitchers; the season is their sum. Every player has a row.
- `pitchstat`, `lrpitchstat_lhp`, `lrpitchstat_rhp`: a pitcher's season and
  what left- and right-handed batters did against him. Pitchers only.
- `careerstats` (every player, a batting career) and `pitchcareer` (pitchers).

`rows` fills them from the provider's season line (`MVPPlayerRecord.line`). A
player without a line gets a row of zeros, as the disc's own players without a
season have (Jon Dowd), never the numbers of the player whose id he took. A
count ESPN gives for the season but not by hand (intentional walks, sacrifice
bunts and flies) is shared between the two sides by their at-bats; a line
without splits goes whole to the right-handers' table. Without a career the
season stands as the career.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...core.hockey import stat
from .models import (
    ATTRIB_FIRST_NAME,
    ATTRIB_LAST_NAME,
    PITCHER_STAT_TABLES,
    STAT_TABLES,
    MVPPlayerRecord,
)

#: Each table's count columns, as its header names them, from column 2.
COLUMNS: dict[str, tuple[str, ...]] = {
    "batstat": ("g", "r", "sb", "cs"),
    "fieldstat": ("g", "po", "a", "e"),
    "lrbatstat": ("1b", "2b", "3b", "hr", "rbi", "so", "bb", "ibb", "sh", "sf", "hbp", "tpa"),
    "pitchstat": ("g", "gs", "r", "er", "w", "l", "sv", "blsv", "cg", "sho", "pk", "ip", "pip"),
    "lrpitchstat": ("so", "bb", "ibb", "hb", "sacs", "sh", "1b", "2b", "3b", "hr", "tpa"),
    "careerstats": ("g", "ab", "h", "hr", "rbi", "r", "sb"),
    "pitchcareer": ("g", "ip", "pip", "er", "w", "l", "sv", "so", "bb"),
}
#: The split tables' sides and the season line's prefix for each.
SIDES = {"lhp": "vsL", "rhp": "vsR"}
Line = Mapping[str, object]


def _count(line: Line, key: str) -> int:
    return round(stat(line, key) or 0)


def _innings(line: Line, key: str) -> tuple[int, int]:
    """Whole innings and thirds: ESPN writes 107 and 2/3 as 107.2."""
    ip = stat(line, key) or 0.0
    return int(ip), round((ip - int(ip)) * 10)


def _by_side(line: Line, counts: tuple[str, ...], shared: tuple[str, ...]) -> dict[str, list[int]]:
    """`counts` then `shared` for each side: the split's own counts, and the
    season's `shared` counts divided by the sides' at-bats (the left-handers'
    share rounded, the right-handers' the rest). A line without splits gives
    the right-handers the season."""
    at_bats = {side: stat(line, f"{prefix}.AB") for side, prefix in SIDES.items()}
    if None in at_bats.values():
        season = [_count(line, k) for k in (*counts, *shared)]
        return {"lhp": [0] * len(season), "rhp": season}
    total = sum(at_bats.values()) or 1  # type: ignore[arg-type]
    left = [round(_count(line, k) * (at_bats["lhp"] or 0) / total) for k in shared]
    out = {"lhp": left, "rhp": [_count(line, k) - n for k, n in zip(shared, left, strict=True)]}
    return {
        side: [_count(line, f"{prefix}.{k}") for k in counts] + out[side]
        for side, prefix in SIDES.items()
    }


def _batting_sides(line: Line) -> dict[str, list[int]]:
    """The `lrbatstat` counts of a batter's line, by side."""
    sides = _by_side(
        line, ("AB", "H", "2B", "3B", "HR", "RBI", "SO", "BB", "HBP"), ("IBB", "SH", "SF")
    )
    out = {}
    for side, (ab, h, d, t, hr, rbi, so, bb, hbp, ibb, sh, sf) in sides.items():
        out[side] = [
            h - d - t - hr,
            d,
            t,
            hr,
            rbi,
            so,
            bb,
            ibb,
            sh,
            sf,
            hbp,
            ab + bb + hbp + sh + sf,
        ]
    return out


def _pitching_sides(line: Line) -> dict[str, list[int]]:
    """The `lrpitchstat` counts of a pitcher's line, by the batter's side. The
    season line calls strikeouts `K` and hit batsmen `BH` where the splits,
    batting lines, say `SO` and `HBP`."""
    if stat(line, "vsL.AB") is None:
        line = {**line, "SO": line.get("K"), "HBP": line.get("BH")}
    sides = _by_side(line, ("AB", "H", "2B", "3B", "HR", "SO", "BB", "HBP"), ("IBB", "SF", "SH"))
    out = {}
    for side, (ab, h, d, t, hr, so, bb, hbp, ibb, sf, sh) in sides.items():
        out[side] = [so, bb, ibb, hbp, sf, sh, h - d - t - hr, d, t, hr, ab + bb + hbp + sf + sh]
    return out


def _career(line: Line, keys: tuple[str, ...]) -> list[int]:
    """`keys` of his career, or of the season when the line has no career."""
    prefix = "career." if stat(line, "career.GP") is not None else ""
    return [_count(line, prefix + k) for k in keys]


def rows(player: MVPPlayerRecord) -> dict[str, dict[int, str]]:
    """Every statistics table's row for one player: `{table: {column: value}}`.
    A batter has none in `PITCHER_STAT_TABLES`. A line without at-bats (a
    leaders-only one) counts as none."""
    line: Line = player.line if stat(player.line, "AB") is not None else {}
    counts: dict[str, list[int]] = {
        "fieldstat": [
            _count(line, f"fielding.{k}") for k in ("gamesPlayed", "putouts", "assists", "errors")
        ],
    }
    if player.is_pitcher:
        batting = {side: [0] * len(COLUMNS["lrbatstat"]) for side in SIDES}
        pitching = _pitching_sides(line)
        career = _career(line, ("GP", "IP", "ER", "W", "L", "SV", "K", "BB"))
        prefix = "career." if stat(line, "career.GP") is not None else ""
        counts["batstat"] = [_count(line, "GP"), 0, 0, 0]
        counts["careerstats"] = [career[0], 0, 0, 0, 0, 0, 0]
        counts["pitchstat"] = [
            *(
                _count(line, k)
                for k in ("GP", "GS", "R", "ER", "W", "L", "SV", "BLSV", "CG", "SHO")
            ),
            # ESPN's `POA`, the pickoff count `stat_mapper` rates him on.
            _count(line, "POA"),
            *_innings(line, "IP"),
        ]
        counts["pitchcareer"] = [career[0], *_innings(line, prefix + "IP"), *career[2:]]
        for side in SIDES:
            counts[f"lrpitchstat_{side}"] = pitching[side]
    else:
        batting = _batting_sides(line)
        counts["batstat"] = [_count(line, k) for k in ("GP", "R", "SB", "CS")]
        counts["careerstats"] = _career(line, ("GP", "AB", "H", "HR", "RBI", "R", "SB"))
    for side in SIDES:
        counts[f"lrbatstat_{side}"] = batting[side]
    assert set(counts) == set(STAT_TABLES) - set(() if player.is_pitcher else PITCHER_STAT_TABLES)
    names = {ATTRIB_FIRST_NAME: player.first_name, ATTRIB_LAST_NAME: player.last_name}
    return {
        table: names | {col: str(max(0, n)) for col, n in enumerate(values, 2)}
        for table, values in counts.items()
    }
