"""The statistics an EA NHL disc of the PS2/PSP era keeps of each player.

Player Information's Season Stats and Playoff Stats pages list a player's NHL
seasons from four tables of `nhlstats.tdb`, one row a season, keyed by the
bio's id (`INDX`): `SCSP` and `SCPP` a skater's regular seasons and playoffs,
`SCSG` and `SCPG` a goalie's. `nhltourneystats.tdb` keeps his international
tournaments the same way (`ITPS`, `ITGS`), and `nhlawards.tdb`'s `SAWA` links
some trophy winners to a bio (`PID_`).

A bio taken over by a new player would show the old one's seasons, so
`rewrite` replaces them with his own, newest first when the table, which is
full on the disc, has no room for them all, and `forget` drops what no
provider knows of him: his tournaments and trophies. Every table keeps the
record count the disc gave it (`_fill`): NHL 07 shows no one's seasons
otherwise. The pages leave out every season after the game's own last one,
so the seasons come here already moved into the game's calendar
(`nhl05_ps2.stat_mapper.set_career`). A player without a season shows what the
disc shows for a rookie, no rows.

`YEAR` is the season's closing year less 1959 (2005-06 is 47), `TEAM` the
club's abbreviation, several joined by `-` for a traded player. `PLMN` is
signed, in two's complement. The disc keeps a shooting % whole (`SHTP`), a
goals-against average cut to its whole part (`GAA_`) and a save % cut to 0
(`SVPC`): the pages work them out from the counts.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence

from ..formats.ea_tdb import TDBFile, TDBTable

#: The members of `db.viv` this module rewrites: seasons, tournaments, trophies.
TDBS = ("nhlstats.tdb", "nhltourneystats.tdb", "nhlawards.tdb")
#: (goalie, playoffs) -> the table of `nhlstats.tdb`.
TABLES = {
    (False, False): "SCSP",
    (False, True): "SCPP",
    (True, False): "SCSG",
    (True, True): "SCPG",
}
YEAR_BASE = 1959
#: A table's column for each stat key of a season (`sports.nhl`'s careers, and
#: the season lines' own keys).
SKATER_COLUMNS = {
    "GP": "GP__",
    "G": "G___",
    "A": "A___",
    "PTS": "PTS_",
    "PIM": "PIM_",
    "S": "SHOT",
    "PPG": "PPG_",
    "SHG": "SHG_",
    "GWG": "GWG_",
}
GOALIE_COLUMNS = {
    "GP": "GP__",
    "W": "W___",
    "L": "L___",
    "T": "T___",
    "SO": "SO__",
    "GA": "GA__",
    "SA": "SA__",
    "MIN": "MIN_",
    "G": "G___",
    "A": "A___",
    "PIM": "PIM_",
}
#: The tournament tables and the bio link of the trophy table.
TOURNAMENT_TABLES = ("ITPS", "ITGS")


def _number(season: Mapping[str, object], key: str) -> int:
    value = season.get(key)
    return round(value) if isinstance(value, int | float) else 0


#: ESPN's name for a stat key, where its season line has no such key.
ESPN_NAMES = {
    "S": "SOG",
    "PPG": "powerPlayGoals",
    "SHG": "shortHandedGoals",
    "GWG": "gameWinningGoals",
    "L": "losses",
    "GA": "goalsAgainst",
    "SA": "shotsAgainst",
}


def season_line(
    line: Mapping[str, object], season: int, team: str, goalie: bool
) -> dict[str, object] | None:
    """One regular season (`season` its start year) from a provider's stat
    line, the NHL API's keys or ESPN's, for a player whose career was not
    fetched; None for a line without a game."""
    games = _number(line, "GP")
    if games <= 0:
        return None
    out: dict[str, object] = {"season": season, "playoffs": False, "team": team, "GP": games}
    for key in GOALIE_COLUMNS if goalie else SKATER_COLUMNS:
        name = key if key in line else ESPN_NAMES.get(key, key)
        if name in line:
            out[key] = _number(line, name)
    if goalie:
        gaa = line.get("GAA")
        if "MIN" not in out and "timeOnIce" in line:  # ESPN's, in seconds
            out["MIN"] = round(_number(line, "timeOnIce") / 60)
        elif "MIN" not in out and "GA" in out and isinstance(gaa, int | float) and gaa > 0:
            out["MIN"] = round(_number(out, "GA") * 60 / gaa)
    else:
        out["+/-"] = _number(line, "+/-")
    return out


def _row(
    table: TDBTable, bio: int, season: Mapping[str, object], goalie: bool
) -> dict[str, object]:
    """The record of one season, for the columns `table` has."""
    width = {f.name: f.bit_width for f in table.fields}
    values: dict[str, object] = {
        "INDX": bio,
        "YEAR": _number(season, "season") + 1 - YEAR_BASE,
        "TEAM": str(season.get("team") or "")[: width.get("TEAM", 0) // 8 - 1],
    }
    columns = GOALIE_COLUMNS if goalie else SKATER_COLUMNS
    for key, column in columns.items():
        values[column] = _number(season, key)
    if goalie:
        minutes, against, shots = (_number(season, k) for k in ("MIN", "GA", "SA"))
        values["PTS_"] = _number(season, "G") + _number(season, "A")
        values["GAA_"] = against * 60 // minutes if minutes else 0
        values["SVPC"] = int(shots > 0 and against == 0)
    else:
        shots, goals = _number(season, "S"), _number(season, "G")
        values["SHTP"] = round(goals * 100 / shots) if shots else 0
        values["GTG_"] = 0
        values["PLMN"] = _number(season, "+/-") % (1 << width.get("PLMN", 7))
    return {name: value for name, value in values.items() if name in width}


def _keep(table: TDBTable, column: str, gone: set[int]) -> int:
    """Drop every live record of `table` whose `column` is in `gone`, closing
    the gaps and zeroing what the table no longer uses. Returns the count the
    table had, for `_fill`."""
    size = table.record_size
    count = min(table.num_records, table.capacity)
    kept = [i for i in range(count) if table.read_record(i).get(column) not in gone]
    data = b"".join(bytes(table._raw_data[i * size : (i + 1) * size]) for i in kept)
    table._raw_data[: count * size] = data + bytes(count * size - len(data))
    table.num_records = len(kept)
    return count


def _fill(table: TDBTable, count: int) -> None:
    """Bring `table` back to `count` records with rows no bio owns (`INDX` all
    ones): NHL 07 shows no one's seasons once a table holds fewer records than
    the disc gave it, every player's rows still there (seen in PPSSPP)."""
    width = next((f.bit_width for f in table.fields if f.name == "INDX"), 0)
    while table.num_records < count:
        table.write_record(table.allocate_record(), {"INDX": (1 << width) - 1})


def rewrite(
    stats: TDBFile,
    careers: Mapping[int, tuple[bool, Sequence[Mapping[str, object]]]],
    listed: Collection[int] | None = None,
) -> tuple[int, int]:
    """Replace the seasons of every bio id in `careers` (goalie or not, and his
    seasons) with his own, and drop those of the bios not in `listed` (the
    players some list still shows; None keeps them all): the tables are full
    on the disc, and the players who left every list make the room. Returns
    how many seasons went in and how many, the oldest, found no room."""
    written = left = 0
    for (goalie, playoffs), name in TABLES.items():
        table = stats.get_table(name)
        if table is None:
            continue
        gone = set(careers)
        if listed is not None:
            held = {table.read_record(i).get("INDX") for i in range(table.num_records)}
            gone |= {bio for bio in held if isinstance(bio, int) and bio not in listed}
        count = _keep(table, "INDX", gone)
        rows = [
            (bio, season)
            for bio, (is_goalie, seasons) in careers.items()
            if is_goalie == goalie
            for season in seasons
            if bool(season.get("playoffs")) == playoffs and _number(season, "GP") > 0
        ]
        rows.sort(key=lambda row: -_number(row[1], "season"))
        room = table.capacity - table.num_records
        for bio, season in rows[:room]:
            table.write_record(table.allocate_record(), _row(table, bio, season, goalie))
        _fill(table, count)
        written += min(room, len(rows))
        left += max(0, len(rows) - room)
    return written, left


def forget(tournaments: TDBFile | None, awards: TDBFile | None, bios: Iterable[int]) -> None:
    """Drop the tournaments of the bio ids `bios`, and unlink their trophies:
    the winner's name stays in the list, as on the rows the disc links to no
    one."""
    gone = set(bios)
    for name in TOURNAMENT_TABLES:
        table = tournaments.get_table(name) if tournaments is not None else None
        if table is not None:
            _fill(table, _keep(table, "INDX", gone))
    table = awards.get_table("SAWA") if awards is not None else None
    if table is not None:
        for i in range(min(table.num_records, table.capacity)):
            if table.read_record(i).get("PID_") in gone:
                table.write_record(i, {"PID_": 0})
