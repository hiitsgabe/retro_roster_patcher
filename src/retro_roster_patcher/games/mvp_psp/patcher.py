"""MVP Baseball (PSP) on the unified Patcher interface.

It patches CSV text inside compressed sections, not byte offsets and not
bit-packed TDB records: a write changes a column of a line of ASCII, then the
whole table is re-serialised, recompressed with RefPack and put back in a
fixed-size hole. A wrong column number writes a real column with the wrong
meaning, and a table that compresses worse after the edit than before cannot be
stored at all (`rom_writer.SectionTooLargeError`).

Player ids are recycled from the disc, not invented. MVP's tables link by
nine-hex-digit ids: the four attribute tables and the nine statistics tables
(`stats`) all key on them, and every written player's row in each is his own.
See `_HashPool`.

`analyze_rom` and `patch` do not apply the same checks, deliberately:
`MVPPSPRomReader.validate_deep` is a heuristic and guards `analyze_rom` only,
`_database_big_extent_fits` is arithmetic and guards both.
"""

from __future__ import annotations

import os
from datetime import date
from itertools import zip_longest
from pathlib import Path
from typing import Any

from ...core.assets import crest_png
from ...core.concurrency import parallel_map
from ...core.errors import ApiError, RomError, as_rom_error
from ...core.headshots import Face, load_headshot, measure_faces
from ...core.looks import pick
from ...core.models import (
    MappedRosters,
    PartialFn,
    PatchResult,
    ProgressFn,
    RomInfo,
    RomSlot,
    SlotMapping,
    StatusFn,
)
from ...core.patcher import Patcher
from ...core.registry import register
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.models import League, LeagueData, Player, TeamRoster
from . import logos, looks, portraits, stats
from .models import (
    ATTRIB_AUDIO_ID,
    ATTRIB_BASERUNNING,
    ATTRIB_BATS,
    ATTRIB_BATTING_STANCE,
    ATTRIB_BIRTHDAY,
    ATTRIB_BODY_TYPE,
    ATTRIB_BONE_PROFILE,
    ATTRIB_BUNTING,
    ATTRIB_CONTRACT_LENGTH,
    ATTRIB_DURABILITY,
    ATTRIB_FIELDING,
    ATTRIB_FIRST_NAME,
    ATTRIB_HEIGHT,
    ATTRIB_HIDDEN,
    ATTRIB_JERSEY,
    ATTRIB_LAST_NAME,
    ATTRIB_PHOTO,
    ATTRIB_PLATE_DISCIPLINE,
    ATTRIB_PRIMARY_POS,
    ATTRIB_RANGE,
    ATTRIB_SALARY,
    ATTRIB_SECONDARY_POS,
    ATTRIB_SPEED,
    ATTRIB_STARPOWER,
    ATTRIB_STEALING_AGGRESSIVE,
    ATTRIB_SWING_TYPE,
    ATTRIB_THROW_ACCURACY,
    ATTRIB_THROW_STRENGTH,
    ATTRIB_THROWS,
    ATTRIB_WEIGHT,
    BATTERS_PER_TEAM,
    BENCH_POSITION,
    BIRTHDAY_EPOCH,
    BULLPEN_POSITIONS,
    DEFAULT_POS_NUM,
    DISC_SEASON,
    HASH_ID_CHARS,
    HEIGHT_BASE,
    LINEUP_POSITIONS,
    LR_CHASE,
    LR_CONTACT,
    LR_DIRECTION,
    LR_FB_PCT,
    LR_FIRST_NAME,
    LR_GB_PCT,
    LR_HR_PCT,
    LR_LAST_NAME,
    LR_LD_PCT,
    LR_MISS,
    LR_POWER,
    LR_TAKE,
    LR_ZONES,
    MVP_TEAM_ABBREVS,
    MVP_TEAM_ORDER,
    NO_PITCH,
    NOT_IN_LINEUP,
    PA_FIRST_NAME,
    PA_LAST_NAME,
    PA_PICKOFF,
    PA_PITCH1_CONTROL,
    PA_PITCH1_DESC,
    PA_PITCH1_MOVEMENT,
    PA_PITCH1_VELOCITY,
    PA_PITCH2_TYPE,
    PA_PITCH_SLOTS,
    PA_PITCH_STRIDE,
    PA_PITCHER_DELIVERY,
    PA_STAMINA,
    PITCHER_STAT_TABLES,
    PITCHERS_PER_TEAM,
    PLAYERS_PER_TEAM,
    POS_STRING_TO_NUM,
    ROSTER_LH_AL_ORDER,
    ROSTER_LH_AL_POS,
    ROSTER_LH_NL_ORDER,
    ROSTER_LH_NL_POS,
    ROSTER_PLAYERID,
    ROSTER_RH_AL_ORDER,
    ROSTER_RH_AL_POS,
    ROSTER_RH_NL_ORDER,
    ROSTER_RH_NL_POS,
    ROSTER_TEAMID,
    ROTATION_POSITIONS,
    STARTERS_PER_TEAM,
    TEAM_COUNT,
    TEAM_HASHES,
    TEAM_RENAMES,
    WEIGHT_BASE,
    ZONE_COLD,
    ZONE_HOT,
    MVPPlayerRecord,
    database_big_extent,
)
from .rom_reader import MVPPSPRomReader, Table
from .rom_writer import MVPPSPRomWriter
from .speech import Voices, named_ids
from .spreads import Spreads, batting_group
from .stat_mapper import LeagueStats, MVPStatMapper

# Where the roster-writing phase of `patch` ends on the progress bar. The copy
# is the slowest step -- a PSP UMD image is hundreds of megabytes -- but it runs
# last, after every record is staged in memory, so it is only the tail.
PROGRESS_RECORDS_END = 0.9


TEAM_ARTID = 6  # `team` column: the club's logo is `logos` file l{artid - 1:03d}

#: The split tables, against a left- and a right-handed pitcher.
LR_TABLES = {"lhp": "lrattrib_lhp", "rhp": "lrattrib_rhp"}

# Seasons of service before MLB's salary arbitration; ESPN's `experience`.
PRE_ARBITRATION_YEARS = 3

#: `attrib` ratings (`stat_mapper.BATTER_SOURCES`) and their columns.
ATTRIB_RATINGS = {
    "plate_discipline": ATTRIB_PLATE_DISCIPLINE,
    "bunting": ATTRIB_BUNTING,
    "stealing": ATTRIB_STEALING_AGGRESSIVE,
    "baserunning": ATTRIB_BASERUNNING,
    "speed": ATTRIB_SPEED,
    "fielding": ATTRIB_FIELDING,
    "range": ATTRIB_RANGE,
    "throw_strength": ATTRIB_THROW_STRENGTH,
    "throw_accuracy": ATTRIB_THROW_ACCURACY,
    "durability": ATTRIB_DURABILITY,
    "starpower": ATTRIB_STARPOWER,
}
#: Ranked, and spread, over his position rather than all batters.
BY_POSITION = frozenset({"fielding", "range", "throw_strength", "throw_accuracy"})


def _database_big_extent_fits(rom_path: Path) -> bool:
    """Is the file long enough to contain the whole of `database.big`?

    `database.big` starts at sector 334 832 of a 2048-byte-sector image and runs
    for 386 977 bytes, so it occupies

        [ 334832 * 2048 , 334832 * 2048 + 386977 )  =  [685735936, 686122913)

    and the file must be at least 686 122 913 bytes long. Unverified against a
    real disc: no ISO may enter this repository.

    What this buys is a diagnosis, not protection -- `MVPPSPRomReader.load`
    already refuses a short read. `load` answers one boolean for four different
    facts, so without this check a user with a truncated download is told their
    disc is the wrong game. Redundant in `analyze_rom`, where both branches
    return the same `RomInfo(is_valid=False)`, and kept so both callers state
    the same bound.
    """
    _, end = database_big_extent()
    try:
        return os.path.getsize(rom_path) >= end
    except OSError:
        return False


_OUTFIELD = ("LF", "CF", "RF")
_INFIELD = ("2B", "3B", "SS")
_NEAR = {pos: _OUTFIELD for pos in ("OF", *_OUTFIELD)}
_NEAR |= {pos: _INFIELD for pos in ("IF", *_INFIELD)}


def _bat_in_lineup_order(records: list[MVPPlayerRecord], lineup: list[dict]) -> None:
    """Renumber the nine starters' batting order by where each has batted in
    the club's recent games (`lineup`: average spot by player id); a starter
    with no starts bats after those with some, in his slot's order."""
    spot = {int(entry["id"]): float(entry["order"]) for entry in lineup}
    nine = [r for r in records if r.batting_order != NOT_IN_LINEUP]
    ranked = sorted(nine, key=lambda r: (spot.get(r.player_id, 10.0), r.batting_order))
    for order, record in enumerate(ranked, 1):
        record.batting_order = order


def _lineup_positions(own: list[str], wanted: tuple[str, ...]) -> list[str]:
    """`own`, the starters' positions in order, made to hold each of `wanted` once.

    A player keeps his own position while it is free. The others (duplicates,
    OF, IF, anything the game has no lineup position for) take what is missing:
    outfielders and infielders first, from their own part of the field when it
    has a gap.
    """
    out = list(own)
    free = [pos for pos in wanted if pos not in own]
    spare = [i for i, pos in enumerate(own) if pos not in wanted or pos in own[:i]]
    for i in sorted(spare, key=lambda i: own[i] not in _NEAR):
        out[i] = next((pos for pos in _NEAR.get(own[i], ()) if pos in free), free[0])
        free.remove(out[i])
    return out


def _as_of_disc(birth_date: str, season: int) -> str:
    """A birth date moved back by the seasons since the disc's: the game's
    calendar stands in 2005, so a player born in 1999 would be 5 (and its Edit
    Player screen shows 1961), where this way he is his age in `season`."""
    try:
        born = date.fromisoformat(birth_date)
    except ValueError:
        return birth_date
    year = born.year - ((season or date.today().year) - DISC_SEASON)
    return born.replace(
        year=year, day=min(born.day, 28) if born.month == 2 else born.day
    ).isoformat()


def _is_starter(slot: int) -> bool:
    """Slots 15-19 are the rotation."""
    return BATTERS_PER_TEAM <= slot < BATTERS_PER_TEAM + STARTERS_PER_TEAM


class _HashPool:
    """Hands one club the player ids of its own disc rows, a player his own kind's.

    Thirteen tables key on a player's id, and a pitcher's has a row in the
    five only pitchers have (`pitchattrib` and the four pitching statistics
    tables), which is why kinds are matched: a batter handed a pitcher's id
    has those rows dropped, a pitcher handed a batter's has them added at the
    tables' end.

    The ids are the club's own (issue #18). Rows the patch keeps list players by
    id too: the retail disc has 36 sides of 25 rows, the 30 clubs then the AL
    and NL All-Stars, MVP Legends, MVP Heroes and EA's two Burnaby sides. One
    pool over the whole `attrib` table handed those sides' players, and other
    clubs', to today's squads, so the Legends and Burnaby sides showed today's
    players and some took a Legend's hidden flag. A club's own ids are listed
    only by its own rows and, for its 2005 All-Stars, by All-Star rows; the
    disc has no spare records to give those back (its 851 `attrib` records are
    the 850 rostered players and a "Default" template, and the sections have no
    room for 50 more), so an All-Star row names today's player at the 2005
    player's position on the 2005 player's club: an id goes to a player of the
    same kind and, first, of the same `attrib` position.

    Three tiers, all preserved, because every alternative loses a player:

      1. An id of the player's own kind, the same position first.
      2. Cross-fall: the club has none of his kind left, so he takes the other
         kind's. Counted as `crossed`.
      3. Synthesis: the club's ids are spent (a squad longer than its disc
         block) and an id is manufactured from the team and player index.
         Counted as `synthesised`.

    A synthesised id is `00`, two hex digits of team index, five of player
    index, then `ff` -- eleven characters, where every id in this repository is
    nine (`models.HASH_ID_CHARS`), so it cannot collide with a real one, and the
    (team, player index) pair is unique within a run so two cannot collide with
    each other. It costs the opposite of a collision: the id matches nothing, so
    the player has no row in any statistics table at all.
    """

    def __init__(
        self,
        ids: list[str],
        pitcher_ids: set[str],
        positions: dict[str, str] | None = None,
        kept: set[str] | None = None,
    ) -> None:
        # In the club's row order, so a run is deterministic given a disc.
        # Membership of `pitchattrib` is what makes an id a pitcher's;
        # `positions` is each id's `attrib` primary position; `kept` the ids
        # rows the patch keeps (the All-Stars) still list.
        self.left = list(ids)
        self._kept = kept or set()
        self._pitchers = pitcher_ids
        self._positions = positions or {}
        self.crossed = 0
        self.synthesised = 0

    def take(
        self, *, is_pitcher: bool, team_index: int, player_index: int, position: str = ""
    ) -> str:
        """One id for one player, degrading through the three tiers above."""
        if not self.left:
            self.synthesised += 1
            return f"00{team_index:02x}{player_index:05x}ff"

        # `min` keeps the first of equals, so row order breaks ties. A kept row's
        # id goes to a player of its own kind first and never to a cross-fall:
        # a club's spare batter took its first pitcher id, often its All-Star's,
        # and the AL All-Stars opened with a second baseman in PPSSPP.
        def rank(h: str) -> tuple[bool, bool, bool, bool]:
            wrong = (h in self._pitchers) != is_pitcher
            return (
                wrong,
                wrong and h in self._kept,
                self._positions.get(h) != position,
                h not in self._kept,
            )

        taken = min(self.left, key=rank)
        self.left.remove(taken)
        self.crossed += (taken in self._pitchers) != is_pitcher
        return taken


@register(
    "mvp-psp",
    platform="psp",
    sport="baseball",
    requires_slot_mapping=False,
    providers=("espn",),
)
class MVPPSPPatcher(Patcher):
    """Teams map to ROM slots by abbreviation, so no manual mapping step.

    `requires_slot_mapping=False` because all 30 slots are real MLB clubs and
    `MODERN_MLB_TO_MVP` covers every one of them, naming no club it has no slot
    for; there is nothing a user could usefully choose. One provider: ESPN is
    the only one in this library with MLB rosters.
    """

    #: `patch` draws each club's current crest as its logo unless `logos=False`.
    supports_logos: bool = True

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        provider: str | None = None,
        on_status: StatusFn | None = None,
        on_partial: PartialFn | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        super().__init__(
            cache_dir,
            provider=provider,
            on_status=on_status,
            on_partial=on_partial,
        )
        self._transport = transport
        # Faces measured from head shots by `patch`, by provider player id.
        self._faces: dict[int, Face] = {}
        # Which audio id each written player takes (`speech`), set by `patch`.
        self._voices = Voices()
        # The players given one of the disc's spare portraits, set by `patch`.
        self._photos: set[int] = set()
        self.mapper = MVPStatMapper()
        # The client creates its cache directory in its own constructor, so
        # constructing this patcher can raise `StorageError`.
        self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        """Inspect an ISO and list its team slots.

        `MVPPSPRomReader.validate_deep` is a heuristic and guards this method
        and not `patch`: a false positive here costs a user every large image
        they own, because `retro-roster analyze` probes every registered patcher
        against one file, while a false negative costs only auto-detection.
        `_database_big_extent_fits` is arithmetic and guards both.

        `extra` carries two ROM-derived numbers unreachable once the reader is
        gone: how many of the nineteen sections decompressed, and how many
        records `attrib` holds -- the size of the id pool `patch` draws from.

        Raises:
            RomError: the file is missing or unreadable.
        """
        with as_rom_error(rom_path):
            size = os.path.getsize(rom_path)
            reader = MVPPSPRomReader(str(rom_path))
            if not reader.load() or not _database_big_extent_fits(rom_path):
                # Must not raise: `analyze` probes every registered patcher
                # against one image.
                return RomInfo(
                    path=str(rom_path),
                    size=size,
                    game_id=self.game_id,
                    is_valid=False,
                )
            info = reader.get_info(deep=True)

        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=info.is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    # The first player the disc's `roster` table lists for this
                    # team. Empty when the slot has no roster rows, or when the
                    # player it names has no `attrib` record.
                    current_name=slot.first_player,
                    display_name=MVP_TEAM_ORDER[slot.index],
                )
                for slot in info.team_slots
            ],
            extra={
                # `sections` and not `records`: the two are the same length on
                # any state reachable here, and this one is the count of streams
                # that decompressed, which is what comparing two discs wants.
                "sections_read": len(reader.sections),
                "attrib_records": len(reader.records.get("attrib", {})),
            },
        )

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        """Fetch every MLB team that has a ROM slot, with its per-player stats.

        Raises:
            ApiError: the provider returned no teams, or none with a ROM slot.
        """
        self.status("Fetching MLB teams...")
        teams = self.api.get_mlb_teams()
        if not teams:
            raise ApiError("The provider returned no MLB teams")

        # Only teams with a slot are worth fetching: the rest cost two network
        # round trips each and are then dropped by `map_rosters`.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches an MVP Baseball PSP ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            # Always pass `season`: the squad endpoint has no season in its URL
            # but does have one in its cache key, so omitting it serves the
            # first season ever fetched for every later season.
            players = self.api.get_baseball_squad(team.id, season)
            leaders = self.api.get_baseball_team_leaders(team.id, season)
            # Where the club's batters have batted lately, for the batting order.
            lineup = self.api.get_baseball_lineup(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # In `extra`, not on the instance: the statistics have to
                    # travel with the rosters through JSON, or a player that
                    # crosses a process boundary takes position defaults.
                    extra={"leaders": leaders or {}, "lineup": lineup or []},
                )
            )

        if on_progress is not None:
            on_progress(1.0, "Complete")
        # Every field but `season` is synthesised: this game has no league
        # endpoint. `teams_count` counts the rosters actually built -- the
        # slot-mapped subset -- not `len(teams)`.
        return LeagueData(
            league=League(
                id=0,
                name="MLB",
                country="USA",
                country_code="US",
                logo_url="",
                season=season,
                teams_count=len(rosters),
            ),
            teams=rosters,
        )

    def suggest_squad_order(self, team_roster):
        """`select_roster`'s order with the nine starters in the batting order
        `map_rosters` gives them (`_bat_in_lineup_order`), so an editor that
        hands this list back with `order_as_given` bats them the same way."""
        leaders = team_roster.extra.get("leaders") or {}
        ordered = self.mapper.select_roster(team_roster.players, leaders)
        spot = {int(e["id"]): float(e["order"]) for e in team_roster.extra.get("lineup") or []}
        nine = [p for p in ordered if not self.mapper.is_pitcher(p)][: len(LINEUP_POSITIONS)]
        batting = iter(sorted(nine, key=lambda p: (spot.get(p.id, 10.0), nine.index(p))))
        ordered = [next(batting) if p in nine else p for p in ordered]
        return self._append_unused(ordered, team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        order_as_given: bool = False,
    ) -> MappedRosters:
        """Reduce league data to a list of `MVPPlayerRecord` per matched slot.

        Sparse: a key exists only for a slot some fetched team mapped to. Each
        value is up to 25 records, batters then pitchers, with `roster_position`
        and `batting_order` already assigned from each one's slot in his own
        block: batters 0-14 (past the fifteenth, the bench), pitchers 15 on.

        With `order_as_given`, the first nine batters field their own
        positions, each one once; see `_lineup_positions`. Otherwise slot `i`
        of the nine fields `LINEUP_POSITIONS[i]`.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, list[MVPPlayerRecord]] = {}
        squads = []
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            # The range test cannot fire here, and is kept: `_write_all_teams`
            # has to make the same test for real, on keys that may have crossed
            # a JSON boundary, and the two must read alike.
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue

            leaders = roster.extra.get("leaders") or {}
            if order_as_given:
                # Batter and pitcher blocks stay; slots 0-8 are the batting
                # order, 15-19 the rotation. Whoever the blocks leave out fills
                # what is short of 25: batters on the bench, pitchers in long
                # relief.
                selected = self._given_blocks(
                    roster.players,
                    [
                        (lambda p: not self.mapper.is_pitcher(p), BATTERS_PER_TEAM),
                        (self.mapper.is_pitcher, PITCHERS_PER_TEAM),
                    ],
                    total=PLAYERS_PER_TEAM,
                )
            else:
                selected = self.mapper.select_roster(roster.players, leaders)
            # Each kind takes its own block's slots, so a short bench does not
            # pull the rotation into it.
            batters = [p for p in selected if not self.mapper.is_pitcher(p)]
            pitchers = [p for p in selected if self.mapper.is_pitcher(p)]
            slots = [min(i, BATTERS_PER_TEAM - 1) for i in range(len(batters))]
            slots += [BATTERS_PER_TEAM + j for j in range(len(pitchers))]
            squads.append(
                (
                    slot,
                    roster,
                    batters,
                    [
                        (player, leaders.get(str(player.id), {}), s)
                        for s, player in zip(slots, batters + pitchers, strict=True)
                    ],
                )
            )
        # Every selected player's line, so each rating ranks a player against
        # the league's, not his club's.
        league = LeagueStats.from_lines(
            (player, line, _is_starter(s)) for *_, members in squads for player, line, s in members
        )
        for slot, roster, batters, members in squads:
            records = [self._map_one(player, line, s, league) for player, line, s in members]
            for record in records:
                record.birth_date = _as_of_disc(record.birth_date, data.league.season)
            if not order_as_given:
                _bat_in_lineup_order(records, roster.extra.get("lineup") or [])
            if order_as_given:
                # The caller's starters field their own positions.
                own = [(p.position or "").upper() for p in batters[: len(LINEUP_POSITIONS)]]
                positions = _lineup_positions(own, LINEUP_POSITIONS)
                for record, position in zip(records, positions, strict=False):
                    record.roster_position = position

            # `MODERN_MLB_TO_MVP` collapses 32 provider codes onto 30 slots --
            # `OAK`/`ATH` and `CWS`/`CHW` -- so two entries in `data.teams` can
            # name one slot. Without this guard an empty alias arriving second
            # wipes the populated record and the run still reports success.
            if not records and teams.get(slot):
                continue
            teams[slot] = records
        return MappedRosters(game_id=self.game_id, teams=teams)

    def _map_one(
        self,
        player: Player,
        stats: dict[str, Any],
        slot: int,
        league: LeagueStats | None = None,
    ) -> MVPPlayerRecord:
        """Map one selected player, given his slot in the 25-slot layout.

        Whether he is mapped as a pitcher comes from his provider position, and
        whether he is mapped as a *starting* pitcher comes from his slot -- 15
        to 19 is the rotation.
        """
        if self.mapper.is_pitcher(player):
            record = self.mapper.map_pitcher(
                player, stats, is_starter=_is_starter(slot), league=league
            )
        else:
            record = self.mapper.map_batter(player, stats, league)
        record.roster_position = self._slot_to_position(slot)
        record.batting_order = slot + 1 if slot < len(LINEUP_POSITIONS) else NOT_IN_LINEUP
        return record

    @staticmethod
    def _slot_to_position(slot: int) -> str:
        """The game's position string for one of the 25 roster slots.

        Slots 0-8 are the batting order, 9-14 the bench, 15-19 the rotation and
        20-24 the bullpen. A slot past 24, a pitcher past the tenth, takes the
        last bullpen role; the disc's own bullpens repeat it too.
        """
        if slot < len(LINEUP_POSITIONS):
            return LINEUP_POSITIONS[slot]
        if slot < BATTERS_PER_TEAM:
            return BENCH_POSITION
        if slot < BATTERS_PER_TEAM + STARTERS_PER_TEAM:
            return ROTATION_POSITIONS[slot - BATTERS_PER_TEAM]
        bullpen_slot = slot - (BATTERS_PER_TEAM + STARTERS_PER_TEAM)
        return BULLPEN_POSITIONS[min(bullpen_slot, len(BULLPEN_POSITIONS) - 1)]

    def patch(
        self,
        *,
        rom_path: Path,
        output_path: Path,
        rosters: MappedRosters,
        on_progress: ProgressFn | None = None,
        **options: Any,
    ) -> PatchResult:
        """Rewrite `database.big`'s roster tables and write the ISO back out.

        Raises:
            RomError: the ISO is missing, unreadable, too short to hold
                `database.big`, not this game by the shallow check, or holds a
                table that cannot be rebuilt within its allocation.
            MappingError: `rosters` was produced by a different patcher.
        """
        # Ahead of every other guard: it costs no I/O, and it prevents the
        # writer choking on another game's record type with an exception
        # outside this library's hierarchy.
        rosters.require_game(self.game_id)

        with as_rom_error(rom_path):
            self.status("Validating ISO...")
            # The arithmetic bound, and not `validate_deep`: see `analyze_rom`.
            if not _database_big_extent_fits(rom_path):
                start, end = database_big_extent()
                raise RomError(
                    f"Not a valid MVP Baseball PSP ISO: {rom_path}: database.big is at bytes "
                    f"{start}-{end} and the file is {os.path.getsize(rom_path)} bytes, so it "
                    f"is not in the image"
                )

            writer = MVPPSPRomWriter(str(rom_path), str(output_path))
            if not writer.load():
                raise RomError(f"Not a valid MVP Baseball PSP ISO: {rom_path}")

            measure = bool(options.get("looks", True))
            urls = {
                p.player_id: p.photo_url
                for players in rosters.teams.values()
                for p in players
                if p.player_id and p.photo_url
            }
            self._faces = (
                measure_faces(self.cache_dir, urls, self._transport, self.status) if measure else {}
            )
            with open(rom_path, "rb") as f:
                self._voices = Voices(
                    named_ids(f),
                    portraits.ids(f) if options.get("portraits", True) else set(),
                    [
                        int(row[ATTRIB_AUDIO_ID])
                        for row in writer.reader.records.get("attrib", {}).values()
                        if row.get(ATTRIB_AUDIO_ID, "").isdigit()
                    ],
                )
            # The disc's spare portraits go round the clubs, each club's first
            # players (its lineup) first, not all to the clubs written first.
            ranked = [
                p.player_id
                for rank in zip_longest(*rosters.teams.values())
                for p in rank
                if p is not None and p.photo_url
            ]
            self._photos = set(ranked[: self._voices.spare])
            self.status("Writing rosters...")
            teams_patched, players_patched = self._write_all_teams(writer, rosters, on_progress)
            if options.get("portraits", True):
                self._portraits(writer, rosters, rom_path)
            if options.get("logos", True):
                self._logos(writer, rosters, rom_path)

            self.status("Saving patched ISO...")
            if on_progress is not None:
                on_progress(PROGRESS_RECORDS_END, "Saving patched ISO...")
            writer.finalize()
            if on_progress is not None:
                on_progress(1.0, "Complete")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    def _write_all_teams(
        self,
        writer: MVPPSPRomWriter,
        rosters: MappedRosters,
        on_progress: ProgressFn | None,
    ) -> tuple[int, int]:
        """Stage every mapped slot's records, returning (teams, players) written.

        The slot range is re-checked here, not just in `map_rosters`: the keys
        come from a plain dict that may have crossed a JSON boundary since.

        The `roster` table is rebuilt rather than merged: every row belonging to
        a team being patched is dropped and replaced, so the club ends up with
        exactly the players it was given and none of its 2005 squad. Rows for
        teams *not* being patched are kept untouched, which is what lets a user
        patch one division and leave the rest of the league alone.
        """
        targets = sorted(
            slot for slot, players in rosters.teams.items() if 0 <= slot < TEAM_COUNT and players
        )
        pitcher_ids = set(writer.reader.records.get("pitchattrib", {}))
        attrib = writer.reader.records.get("attrib", {})
        # Read before any player is written, so the spreads are the disc's.
        spreads = Spreads.of(writer.reader.records, LR_TABLES["lhp"], LR_TABLES["rhp"])
        positions = {h: cols.get(ATTRIB_PRIMARY_POS, "") for h, cols in attrib.items()}

        patched_team_hashes = {TEAM_HASHES[MVP_TEAM_ABBREVS[slot]] for slot in targets}
        old_roster = writer.reader.records.get("roster", {})
        new_roster: Table = {
            rec_id: fields
            for rec_id, fields in old_roster.items()
            if fields.get(ROSTER_TEAMID, "") not in patched_team_hashes
        }
        # A patched club's rows take its own old rows' ids, so they stay where
        # its squad was in the table: the game reads a club's squad from its
        # place there (team select lost the Angels' and the Athletics' location
        # and rank when their rows, the table's first, moved to its end; a
        # squad shorter than its block moved every later club, issue #18). A
        # squad longer than the old one takes fresh ids, counted past every id
        # the disc used.
        kept = {fields.get(ROSTER_PLAYERID, "") for fields in new_roster.values()}
        old_ids: dict[str, list[str]] = {}
        for rec_id, fields in old_roster.items():
            old_ids.setdefault(fields.get(ROSTER_TEAMID, ""), []).append(rec_id)
        roster_counter = max((int(rid, 16) for rid in old_roster), default=0) + 1

        teams_patched = 0
        players_patched = 0
        crossed = synthesised = 0
        short: list[str] = []
        for i, slot in enumerate(targets):
            players: list[MVPPlayerRecord] = rosters.teams[slot]
            if on_progress is not None:
                on_progress(
                    (i / len(targets)) * PROGRESS_RECORDS_END,
                    f"Writing {MVP_TEAM_ORDER[slot]} ({len(players)} players)...",
                )
            team_hash = TEAM_HASHES[MVP_TEAM_ABBREVS[slot]]
            ids = list(old_ids.get(team_hash, []))
            pool = _HashPool(
                [old_roster[rid].get(ROSTER_PLAYERID, "") for rid in ids],
                pitcher_ids,
                positions,
                kept,
            )
            dh_order = next((p.batting_order for p in players if p.roster_position == "DH"), None)
            for player_index, player in enumerate(players):
                player.hash_id = pool.take(
                    is_pitcher=player.is_pitcher,
                    team_index=slot,
                    player_index=player_index,
                    position=str(POS_STRING_TO_NUM.get(player.primary_position, DEFAULT_POS_NUM)),
                )
                self._write_player(writer, player, spreads)
                if ids:
                    rec_id = ids.pop(0)
                else:
                    rec_id = f"{roster_counter:0{HASH_ID_CHARS}x}"
                    roster_counter += 1
                new_roster[rec_id] = self._build_roster_fields(team_hash, player, dh_order)
                players_patched += 1
            # A squad shorter than the club's block: the block keeps its length
            # (see above), its last rows listing the 2005 players nobody took,
            # out of every lineup.
            for rec_id, player_id in zip(ids, pool.left, strict=False):
                new_roster[rec_id] = self._benched_row(
                    team_hash, player_id, player_id in pitcher_ids
                )
            if ids:
                short.append(MVP_TEAM_ORDER[slot])
            crossed += pool.crossed
            synthesised += pool.synthesised
            teams_patched += 1

        writer.update_records("roster", new_roster)
        # A renamed club's location, nickname and abbreviation (`TEAM_RENAMES`).
        teams = dict(writer.reader.records.get("team", {}))
        renamed = [
            MVP_TEAM_ABBREVS[slot]
            for slot in targets
            if MVP_TEAM_ABBREVS[slot] in TEAM_RENAMES
            and TEAM_HASHES[MVP_TEAM_ABBREVS[slot]] in teams
        ]
        for abbrev in renamed:
            row = dict(teams[TEAM_HASHES[abbrev]])
            row.update(zip((1, 2, 3), TEAM_RENAMES[abbrev], strict=True))
            teams[TEAM_HASHES[abbrev]] = row
        if renamed:
            writer.update_records("team", teams)
        if crossed or synthesised:
            # Report the degradation; do not fix it. Every alternative to it
            # drops a player. See `_HashPool`.
            self.status(
                f"{crossed} player(s) took an id from the other position pool and "
                f"{synthesised} were given a synthesised id"
            )
        if short:
            self.status(
                f"Fewer than {PLAYERS_PER_TEAM} players for {', '.join(short)}: "
                f"the rest of each squad keeps 2005 players, out of the lineup"
            )
        return teams_patched, players_patched

    def _logos(self, writer: MVPPSPRomWriter, rosters: MappedRosters, rom_path: Path) -> None:
        """Each patched club's current crest over its team-select logo (`logos`)."""
        teams = writer.reader.records.get("team", {})
        urls = {}
        for team in self.api.get_mlb_teams():
            slot = self.mapper.get_team_slot(team.code)
            if slot is None or slot not in rosters.teams or not team.logo_url:
                continue
            artid = teams.get(TEAM_HASHES[MVP_TEAM_ABBREVS[slot]], {}).get(TEAM_ARTID, "")
            if artid.strip().isdigit():
                urls[int(artid)] = team.logo_url
        crests = dict(
            zip(
                urls,
                parallel_map(
                    lambda u: crest_png(None, None, u, self._transport), list(urls.values())
                ),
                strict=True,
            )
        )
        with open(rom_path, "rb") as f:
            writer.edits += logos.edits(
                f, {a: png for a, png in crests.items() if png is not None}, self.status
            )

    def _portraits(self, writer: MVPPSPRomWriter, rosters: MappedRosters, rom_path: Path) -> None:
        """Each written player's head shot over his record's portrait, the
        silhouette without one (`portraits`)."""
        attrib = writer.reader.records.get("attrib", {})
        players = [p for ps in rosters.teams.values() for p in ps if p.hash_id in attrib]
        shots = parallel_map(
            lambda p: (
                load_headshot(self.cache_dir, p.photo_url, self._transport) if p.photo_url else None
            ),
            players,
        )
        pictures = {
            attrib[p.hash_id].get(ATTRIB_AUDIO_ID, "0"): png
            for p, png in zip(players, shots, strict=True)
            if attrib[p.hash_id].get(ATTRIB_AUDIO_ID, "").strip().isdigit()
        }
        with open(rom_path, "rb") as f:
            writer.edits += portraits.edits(f, pictures, self.status)

    def _write_player(
        self, writer: MVPPSPRomWriter, player: MVPPlayerRecord, spreads: Spreads
    ) -> None:
        """Stage one player's records: `attrib`, both split tables, and
        `pitchattrib` for a pitcher. A batter given a pitcher's recycled id
        loses that pitcher's `pitchattrib` row, as the disc's batters have none.
        """
        fields = _attrib_fields(player, spreads)
        face = self._faces.get(player.player_id)
        looked = looks.columns(face, player.nationality, str(player.player_id or player.hash_id))
        fields.update({col: str(value) for col, value in looked.items()})
        old = writer.reader.records.get("attrib", {}).get(player.hash_id, {})
        if self._voices.named and old.get(ATTRIB_AUDIO_ID, "").isdigit():
            # No 2005 player's name for him, and a photo only if he has a picture.
            photo = player.player_id in self._photos
            audio = self._voices.assign(int(old[ATTRIB_AUDIO_ID]), photo) or 0
            fields[ATTRIB_AUDIO_ID] = str(audio)
            fields[ATTRIB_PHOTO] = "1" if self._voices.pictured(audio) else "2"
        writer.update_player_record("attrib", player.hash_id, fields)
        for side, table in LR_TABLES.items():
            writer.update_player_record(
                table,
                player.hash_id,
                _split_fields(player, spreads, table, side, writer.columns(table)),
            )
        for table, row in stats.rows(player).items():
            writer.update_player_record(table, player.hash_id, row)
        if player.is_pitcher:
            writer.update_player_record(
                "pitchattrib", player.hash_id, _pitch_fields(player, spreads)
            )
        else:
            for table in ("pitchattrib", *PITCHER_STAT_TABLES):
                writer.delete_player_record(table, player.hash_id)

    @staticmethod
    def _benched_row(team_hash: str, player_id: str, is_pitcher: bool) -> dict[int, str]:
        """A `roster` row in no lineup: on the bench, or in long relief."""
        where = BULLPEN_POSITIONS[-1] if is_pitcher else BENCH_POSITION
        return {
            ROSTER_TEAMID: team_hash,
            ROSTER_PLAYERID: player_id,
            ROSTER_RH_AL_POS: where,
            ROSTER_RH_AL_ORDER: str(NOT_IN_LINEUP),
            ROSTER_RH_NL_POS: where,
            ROSTER_RH_NL_ORDER: str(NOT_IN_LINEUP),
            ROSTER_LH_AL_POS: where,
            ROSTER_LH_AL_ORDER: str(NOT_IN_LINEUP),
            ROSTER_LH_NL_POS: where,
            ROSTER_LH_NL_ORDER: str(NOT_IN_LINEUP),
        }

    @staticmethod
    def _build_roster_fields(
        team_hash: str,
        player: MVPPlayerRecord,
        dh_order: int | None,
    ) -> dict[int, str]:
        """One `roster` row: the team, the player, and four lineup assignments.

        The game keeps a separate lineup for each combination of the opposing
        starter's handedness and the host park's designated-hitter rule, so
        four (position, batting order) pairs. Every club fills all four, as on
        the retail disc: an AL club plays at NL parks and an NL club at AL ones
        in interleague and World Series games.

        The AL pair is the player's own. In the NL pair the DH (who batted
        `dh_order`th, None for a lineup without one) sits on the bench, the
        starters behind him move up one, and SP1 bats ninth.

        Inherited: the two handedness variants get the same lineup, so the
        game's platoon feature does nothing for a patched team. Fixing it needs
        a per-handedness split no provider here supplies.
        """
        pos, order = player.roster_position, player.batting_order
        nl_pos, nl_order = pos, NOT_IN_LINEUP
        if pos == "DH":
            nl_pos = BENCH_POSITION
        elif order != NOT_IN_LINEUP:
            nl_order = order - 1 if dh_order is not None and order > dh_order else order
        elif pos == ROTATION_POSITIONS[0]:
            nl_order = len(LINEUP_POSITIONS)
        return {
            ROSTER_TEAMID: team_hash,
            ROSTER_PLAYERID: player.hash_id,
            ROSTER_RH_AL_POS: pos,
            ROSTER_RH_AL_ORDER: str(order),
            ROSTER_RH_NL_POS: nl_pos,
            ROSTER_RH_NL_ORDER: str(nl_order),
            ROSTER_LH_AL_POS: pos,
            ROSTER_LH_AL_ORDER: str(order),
            ROSTER_LH_NL_POS: nl_pos,
            ROSTER_LH_NL_ORDER: str(nl_order),
        }


def _kind(player: MVPPlayerRecord) -> str:
    """His group among the disc's: `B`, `SP` or `RP`."""
    if not player.is_pitcher:
        return "B"
    return "SP" if player.primary_position == "SP" else "RP"


def _position(player: MVPPlayerRecord) -> int:
    return POS_STRING_TO_NUM.get(player.primary_position, DEFAULT_POS_NUM)


def _key(player: MVPPlayerRecord) -> str:
    """What a choice among the disc's shared looks is made on: the same each run."""
    return str(player.player_id or player.hash_id)


def _rate(
    spreads: Spreads,
    player: MVPPlayerRecord,
    table: str,
    column: int | str,
    rating: str,
    group: str,
    flip: bool = False,
) -> int | None:
    """The disc's value at his rank for `rating` (1 - rank with `flip`) in
    `group`'s spread; without a rank, his position's median (the group's for a
    figure no position has, a pitch type's)."""
    share = player.ratings.get(rating)
    if share is None:
        median = spreads.at(table, column, f"pos{_position(player)}", 0.5)
        return median if median is not None else spreads.at(table, column, group, 0.5)
    return spreads.at(table, column, group, 1 - share if flip else share)


def _put(fields: dict[int, str], column: int, value: int | str | None) -> None:
    """Set a column, unless the disc gave nothing to set it to."""
    if value is not None:
        fields[column] = str(value)


def _attrib_fields(player: MVPPlayerRecord, spreads: Spreads) -> dict[int, str]:
    """The `attrib` columns this patcher sets from the player; speech and
    photo are `_write_player`'s, equipment and walk-up music are merged
    through."""
    kind = _kind(player)
    pos = _position(player)
    height, weight = player.height - HEIGHT_BASE, player.weight - WEIGHT_BASE
    fields = {
        ATTRIB_FIRST_NAME: player.first_name,
        ATTRIB_LAST_NAME: player.last_name,
        ATTRIB_JERSEY: str(player.jersey),
        ATTRIB_BATS: str(player.bats),
        ATTRIB_THROWS: str(player.throws),
        ATTRIB_PRIMARY_POS: str(pos),
        ATTRIB_HEIGHT: str(height),
        ATTRIB_WEIGHT: str(weight),
        ATTRIB_HIDDEN: "0",
    }
    secondary = player.secondary_position
    if secondary is None:
        common, _ = spreads.common("attrib", ATTRIB_SECONDARY_POS, f"pos{pos}")
        secondary = common[0] if common else None
    _put(fields, ATTRIB_SECONDARY_POS, secondary)
    if (build := spreads.build(height, weight)) is not None:
        fields[ATTRIB_BONE_PROFILE], fields[ATTRIB_BODY_TYPE] = map(str, build)
    for column in (ATTRIB_BATTING_STANCE, ATTRIB_SWING_TYPE):
        values, weights = spreads.common("attrib", column, kind)
        if values:
            fields[column] = str(values[pick(weights, f"{_key(player)}/{column}")])
    # No provider has his contract (ESPN's roster lists early-career salaries
    # for 35 players, the latest of them 2017's): under three years of service
    # he is paid the disc's lowest salary for a year, as MLB's pre-arbitration
    # players are; otherwise what the disc's players within a year of his age
    # are, on a contract as long as theirs.
    paid_as = [f"pos{pos}"]
    try:
        born = date.fromisoformat(player.birth_date)
        fields[ATTRIB_BIRTHDAY] = str((born - BIRTHDAY_EPOCH).days)
        paid_as = [f"age{DISC_SEASON - born.year + d}" for d in (-1, 0, 1)]
    except (TypeError, ValueError):
        pass
    for column in (ATTRIB_SALARY, ATTRIB_CONTRACT_LENGTH):
        if player.experience < PRE_ARBITRATION_YEARS:
            _put(fields, column, spreads.at("attrib", column, "all", 0.0))
        else:
            _put(fields, column, spreads.middle("attrib", column, paid_as))
    for rating, column in ATTRIB_RATINGS.items():
        name = f"pitch_{rating}" if player.is_pitcher else rating
        group = f"pos{pos}" if rating in BY_POSITION else kind
        _put(fields, column, _rate(spreads, player, "attrib", column, name, group))
    return fields


def _split_fields(
    player: MVPPlayerRecord, spreads: Spreads, table: str, side: str, names: dict[str, int]
) -> dict[int, str]:
    """His columns in one split table: contact, power, the hot and cold cells
    of the zone, where his fly balls go, his balls in play, his approach."""
    group = batting_group(player.bats, side == "lhp")
    fields = {LR_FIRST_NAME: player.first_name, LR_LAST_NAME: player.last_name}

    def rate(column: int | str, rating: str, flip: bool = False) -> int | None:
        return _rate(spreads, player, table, column, f"{rating}_{side}", group, flip)

    _put(fields, LR_CONTACT, rate(LR_CONTACT, "contact"))
    _put(fields, LR_POWER, rate(LR_POWER, "power"))
    hot, cold = rate("hot", "zones"), rate("cold", "zones", flip=True)
    means = [spreads.mean(table, c, group) for c in LR_ZONES]
    if hot is not None and cold is not None and None not in means:
        order = sorted(range(len(LR_ZONES)), key=lambda i: -means[i])  # type: ignore[operator]
        cold = min(cold, len(order) - hot)
        for rank, i in enumerate(order):
            zone = ZONE_HOT if rank < hot else ZONE_COLD if rank >= len(order) - cold else 1
            fields[LR_ZONES[i]] = str(zone)
    direction = [spreads.mean(table, c, group) for c in LR_DIRECTION]
    if None not in direction:
        left, right = round(direction[0]), round(direction[2])  # type: ignore[arg-type]
        fields.update(zip(LR_DIRECTION, map(str, (left, 100 - left - right, right)), strict=True))
    hr, ld, gb = rate(LR_HR_PCT, "hr"), rate(LR_LD_PCT, "ld"), rate(LR_GB_PCT, "gb")
    if hr is not None and ld is not None and gb is not None:
        gb = min(gb, 100 - hr - ld)
        fields |= {LR_HR_PCT: str(hr), LR_LD_PCT: str(ld), LR_GB_PCT: str(gb)}
        fields[LR_FB_PCT] = str(100 - hr - ld - gb)
    for columns, rating in ((LR_CHASE, "chase"), (LR_TAKE, "take"), (LR_MISS, "miss")):
        for column in (names[n] for n in columns if n in names):
            _put(fields, column, rate(column, rating))
    return fields


def _pitch_fields(player: MVPPlayerRecord, spreads: Spreads) -> dict[int, str]:
    """A pitcher's whole `pitchattrib` row: stamina, pickoff, the fastball, his
    other pitches (`pitch_types`) and the slots he has none for, his delivery."""
    kind = _kind(player)
    fields: dict[int, str] = {
        PA_FIRST_NAME: player.first_name,
        PA_LAST_NAME: player.last_name,
        PA_PITCH1_MOVEMENT: "0",
        PA_PITCH1_DESC: "0",
    }

    def rate(column: int | str, rating: str, group: str) -> int | None:
        return _rate(spreads, player, "pitchattrib", column, f"pitch_{rating}", group)

    _put(fields, PA_STAMINA, rate(PA_STAMINA, "stamina", kind))
    _put(fields, PA_PICKOFF, rate(PA_PICKOFF, "pickoff", kind))
    _put(fields, PA_PITCH1_CONTROL, rate(PA_PITCH1_CONTROL, "control", kind))
    fastball = rate(PA_PITCH1_VELOCITY, "velocity", f"{kind}{player.throws}")
    _put(fields, PA_PITCH1_VELOCITY, fastball)
    for slot in range(PA_PITCH_SLOTS):
        base = PA_PITCH2_TYPE + slot * PA_PITCH_STRIDE
        figures: list[int | str | None] = [NO_PITCH] * PA_PITCH_STRIDE
        if slot < len(player.pitch_types):
            kind_code = player.pitch_types[slot]
            drop = spreads.mean("pitchattrib", f"drop{kind_code}", "all")
            descs, _ = spreads.common("pitchattrib", f"desc{kind_code}", f"throws{player.throws}")
            if not descs:
                descs, _ = spreads.common("pitchattrib", f"desc{kind_code}", "all")
            figures = [
                kind_code,
                rate(f"move{kind_code}", "movement", "all"),
                descs[0] if descs else None,
                rate(f"ctrl{kind_code}", "control", "all"),
                fastball - round(drop) if fastball is not None and drop is not None else None,
            ]
            if None in figures:
                figures = [NO_PITCH] * PA_PITCH_STRIDE
        fields |= {base + i: str(v) for i, v in enumerate(figures)}
    # The shared windups are mirrored for a left-hander; his own hand's
    # commonest ones all the same.
    values, weights = spreads.common("pitchattrib", PA_PITCHER_DELIVERY, f"{kind}{player.throws}")
    if not values:
        values, weights = spreads.common("pitchattrib", PA_PITCHER_DELIVERY, kind)
    if values:
        fields[PA_PITCHER_DELIVERY] = str(values[pick(weights, _key(player))])
    return fields
