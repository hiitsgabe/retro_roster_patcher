"""MVP Baseball (PSP) on the unified Patcher interface.

Four things about this game are worth reading before the code.

**It patches CSV text inside compressed sections.** Not byte offsets into a
cartridge, and not bit-packed TDB records: a write here changes a column of a
line of ASCII, the whole table is re-serialised, recompressed with RefPack and
put back in a fixed-size hole. The failure modes follow from that. A wrong
column number writes a real column with the wrong meaning; and a table that
compresses worse after the edit than before cannot be stored at all, which is
`rom_writer.SectionTooLargeError` -- the one inherited bug in this game that this
port does fix, because upstream's `continue` shipped a half-patched disc under a
full success report.

**Player ids are recycled from the disc, not invented.** MVP's tables link to
each other by nine-hex-digit ids, and eight tables this patcher does not write --
`batstat`, `fieldstat`, `careerstats`, `pitchcareer` and the four left/right
split-stat tables -- key on the same ids. So a new player is given an id the
disc already had, and inherits that player's statistical history. `_HashPool`
is where that happens and where the source's three tiers of silent degradation
are argued.

**`fetch`, `map_rosters` and `patch` are genuinely separate here**, and in the
source they were not: `patch_rom` called `map_rosters` itself, so the mapping
step could not be inspected, cached or serialised, and the pygame front end
that wanted to preview a roster had to call `map_rosters` a second time and
throw the result away. `patch` now consumes the `MappedRosters` it is handed.

**`analyze_rom` and `patch` do not apply the same checks**, deliberately.
`MVPPSPRomReader.validate_deep` is a heuristic and guards `analyze_rom` only;
`_database_big_extent_fits` is arithmetic and guards both. See `analyze_rom`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...core.errors import ApiError, RomError, as_rom_error
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
from .models import (
    AL_SLOT_COUNT,
    ATTRIB_BASERUNNING,
    ATTRIB_BATS,
    ATTRIB_BUNTING,
    ATTRIB_DURABILITY,
    ATTRIB_FIELDING,
    ATTRIB_FIRST_NAME,
    ATTRIB_HEIGHT,
    ATTRIB_JERSEY,
    ATTRIB_LAST_NAME,
    ATTRIB_PLATE_DISCIPLINE,
    ATTRIB_PRIMARY_POS,
    ATTRIB_RANGE,
    ATTRIB_SECONDARY_POS,
    ATTRIB_SPEED,
    ATTRIB_STARPOWER,
    ATTRIB_STEALING_AGGRESSIVE,
    ATTRIB_THROW_ACCURACY,
    ATTRIB_THROW_STRENGTH,
    ATTRIB_THROWS,
    ATTRIB_WEIGHT,
    BATTERS_PER_TEAM,
    BENCH_POSITION,
    BULLPEN_POSITIONS,
    DEFAULT_POS_NUM,
    HASH_ID_CHARS,
    LINEUP_POSITIONS,
    LR_CONTACT,
    LR_FIRST_NAME,
    LR_LAST_NAME,
    LR_POWER,
    MAX_EXTRA_PITCHES,
    MVP_TEAM_ABBREVS,
    MVP_TEAM_ORDER,
    NOT_IN_LINEUP,
    PA_FIRST_NAME,
    PA_LAST_NAME,
    PA_PICKOFF,
    PA_PITCH1_CONTROL,
    PA_PITCH1_MOVEMENT,
    PA_PITCH1_VELOCITY,
    PA_PITCH2_TYPE,
    PA_PITCH_CONTROL_OFFSET,
    PA_PITCH_MOVEMENT_OFFSET,
    PA_PITCH_STRIDE,
    PA_PITCH_TYPE_OFFSET,
    PA_PITCH_VELOCITY_OFFSET,
    PA_STAMINA,
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
    MVPPlayerRecord,
    database_big_extent,
)
from .rom_reader import MVPPSPRomReader, Table
from .rom_writer import MVPPSPRomWriter
from .stat_mapper import MVPStatMapper

# Where the roster-writing phase of `patch` starts and ends on the progress
# bar. The copy is the slowest step by far -- a PSP UMD image is hundreds of
# megabytes -- but it happens last here, after every record is already staged in
# memory, so most of the bar is the record work and the tail is the copy.
PROGRESS_RECORDS_END = 0.9


def _database_big_extent_fits(rom_path: Path) -> bool:
    """Is the file long enough to contain the whole of `database.big`?

    This is the **arithmetic bound**, and it guards `analyze_rom` *and* `patch`.
    A file that fails it provably cannot be patched -- the bytes to be rewritten
    are not in it -- so exempting `patch` would let the run report success over
    an image it never touched.

    The arithmetic, explicitly. `database.big` starts at sector 334 832 of a
    2048-byte-sector image and runs for 386 977 bytes, so it occupies

        [ 334832 * 2048 , 334832 * 2048 + 386977 )  =  [685735936, 686122913)

    and the file must be at least 686 122 913 bytes long. Those three numbers
    are the source's, have never been checked against a real disc, and cannot be
    -- no ISO may enter this repository.

    **What this bound is for here is different from what it is for in the two
    NHL disc games, and the difference is worth stating because those two are
    where the check came from.** There, `_db_viv_extent_fits` closes a genuine
    silent-corruption path: `_extract_db_viv` reads short without complaint,
    `refpack_decompress` returns short and never pads, and `TDBFile.serialize`
    then shrinks its own output, so a truncated image is patched into a
    corrupted one that reports success. **That path does not exist in this
    game**, and the migration brief for it says it does. `MVPPSPRomReader.load`
    refuses a read that returned fewer than `DATABASE_BIG_SIZE` bytes, so a
    short image never reaches the decompressor at all, and it refused it
    upstream too.

    What the explicit bound buys instead is a *reason*, and it buys it in
    exactly one of the two callers. `load` answers one boolean for four
    different facts -- the file is gone, the file is unreadable, the file is too
    short, or something raised -- and `patch` has to say which. With this check
    it raises naming both numbers rather than "Failed to load MVP Baseball PSP
    ISO"; without it a user with a truncated download is told their disc is the
    wrong game. That is not redundant ceremony, and it is not load-bearing
    against corruption; it is a diagnosis.

    **In `analyze_rom` it is measurably redundant, and that is stated here
    rather than implied away.** Mutation testing deleted the
    `or not _database_big_extent_fits(rom_path)` clause from `analyze_rom` and
    the whole suite stayed green, which is correct and not a coverage gap:
    `load` only answers True after reading `DATABASE_BIG_SIZE` bytes starting at
    the extent's own offset, so `load() is True` already implies the file
    reaches `end`, and both branches of that `if` return the identical
    `RomInfo(is_valid=False)` anyway. An earlier draft of this docstring claimed
    `analyze_rom` could report the two cases apart. It cannot -- there is no
    field in `RomInfo` where the difference would land. The clause is kept
    because `analyze` probes every registered patcher against one file and the
    arithmetic bound is the statement of what this game needs, but it decides
    nothing there today.

    The bound that *is* load-bearing against silent corruption in this game is
    a different one, and it is in the writer: a rebuilt section must fit its
    fixed allocation. See `rom_writer.SectionTooLargeError`.
    """
    _, end = database_big_extent()
    try:
        return os.path.getsize(rom_path) >= end
    except OSError:
        return False


class _HashPool:
    """Hands out the disc's own player ids, pitchers' to pitchers where it can.

    Ids matter because eight tables this patcher never writes key on them --
    `batstat`, `pitchstat`, `fieldstat`, `careerstats`, `pitchcareer` and the
    four split-stat tables. Reusing an id gives the new player the old player's
    entire statistical history, which is why the pools are split at all: a
    pitcher handed a batter's id gets a batter's career line, and appears in the
    game's own leaderboards as a hitter.

    **The source degraded silently in three tiers and all three are kept**, and
    they are kept rather than fixed for one reason: every alternative loses a
    player. A run that exhausts a pool has more pitchers than the disc had, and
    the choices are to give one a batter's id, to synthesise an id, or to drop
    him. The first two write a player; the third does not. What is added here is
    that the pool *counts* what it did, so `patch` can report it through
    `PatchResult` and a caller is no longer told nothing.

    Tier 1, the normal case: a pitcher takes the next id from the pitcher pool,
    a batter from the batter pool.

    Tier 2, cross-fall: an exhausted pool borrows from the other. This is the
    one with a real cost -- a pitcher wearing a batter's id inherits at-bats and
    no innings -- and it is counted as `crossed`.

    Tier 3, synthesis: both pools are empty and an id is manufactured from the
    team and player index. Counted as `synthesised`.

    **The migration brief said a synthesised id may collide with a real one.
    Measured: it cannot.** The pattern is `00`, two hex digits of team index,
    five of player index, then `ff` -- which is **eleven** characters, where
    every id this repository has ever seen is nine. All thirty of
    `models.TEAM_HASHES` are nine, `models.HASH_ID_CHARS` records it, and the
    generated ids in `tests/fixtures/synthetic_mvp_iso.py` are nine for the
    same reason. Two synthesised ids cannot collide with each other either: the
    (team, player index) pair is unique within a run, thirty teams fit two hex
    digits and twenty-five players fit five.

    What tier 3 actually costs is the opposite of a collision. The id matches
    nothing anywhere, so the player has no row in any of the eight statistics
    tables and no career history at all -- which is arguably *better* than tier
    2, where he inherits somebody else's. Neither is good, and both are
    reported.

    None of this can be checked against a real disc. If a retail `attrib` table
    holds eleven-character ids, the collision the brief describes is back; the
    claim above is a claim about what is in this repository.
    """

    def __init__(self, attrib_ids: list[str], pitcher_ids: set[str]) -> None:
        # Order comes from `attrib`, the table both pools are drawn from, so a
        # run is deterministic given a disc. Membership of `pitchattrib` is what
        # makes an id a pitcher's.
        self._pitchers: Iterator[str] = iter([h for h in attrib_ids if h in pitcher_ids])
        self._batters: Iterator[str] = iter([h for h in attrib_ids if h not in pitcher_ids])
        self.crossed = 0
        self.synthesised = 0

    def take(self, *, is_pitcher: bool, team_index: int, player_index: int) -> str:
        """One id for one player, degrading through the three tiers above."""
        primary, fallback = (
            (self._pitchers, self._batters) if is_pitcher else (self._batters, self._pitchers)
        )
        try:
            return next(primary)
        except StopIteration:
            pass
        try:
            taken = next(fallback)
        except StopIteration:
            self.synthesised += 1
            return f"00{team_index:02x}{player_index:05x}ff"
        self.crossed += 1
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

    `requires_slot_mapping=False` follows the source, which had no mapping step
    and no way to express one: `map_rosters` looked each fetched team up in
    `MODERN_MLB_TO_MVP` and dropped the ones with no entry. Verified for this
    game specifically -- all 30 slots are real MLB clubs, `MODERN_MLB_TO_MVP`
    covers every one of them and names no club it does not have a slot for, so
    there is nothing a user could usefully choose. That is unlike `iss-snes`,
    whose 27 slots are national teams no club abbreviation names.

    One provider. ESPN is the only one with MLB rosters in this library;
    `sports/nhl.py` is hockey-only.
    """

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
        self.mapper = MVPStatMapper()
        # Eagerly, and the client creates its cache directory from its own
        # constructor, so constructing this patcher can raise `StorageError`.
        # Nothing here reaches the network.
        self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        """Inspect an ISO and list its team slots.

        Two checks, guarding different sets of entry points on purpose.

        `MVPPSPRomReader.validate_deep` is a **heuristic**: the three RefPack
        header bytes the source checked, and then whether the `team` table's own
        record ids include any of MVP Baseball's thirty. It guards this method
        and NOT `patch`. A false positive here costs a user every large image
        they own, because `retro-roster analyze` probes every registered patcher
        against one file; a false negative costs only auto-detection, because
        `patch --game mvp-psp` never calls this method. `validate_deep`'s own
        docstring argues why the three bytes alone are not enough against the
        population that matters -- other EA discs of the same era, where a
        RefPack stream at a sector boundary is the house style rather than a
        coincidence.

        `_database_big_extent_fits` is an **arithmetic bound** and guards both.

        `extra` carries two ROM-derived numbers that are unreachable once the
        reader is gone, both JSON-serialisable per `core/models.py`: how many
        of the nineteen sections decompressed, and how many player records the
        `attrib` table holds -- which is the size of the id pool `patch` draws
        from and therefore how many players this disc can hold at all.

        Raises:
            RomError: the file is missing or unreadable.
        """
        with as_rom_error(rom_path):
            size = os.path.getsize(rom_path)
            reader = MVPPSPRomReader(str(rom_path))
            if not reader.load() or not _database_big_extent_fits(rom_path):
                # Readable, and not this game: too short to hold `database.big`
                # at all, or the read failed. `analyze` probes every registered
                # patcher against one image, so this must not raise.
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
                    # The name of the first player the disc's own `roster` table
                    # lists for this team, so two MVP ISOs with different
                    # rosters render differently. Empty when the slot has no
                    # roster rows, or when the player it names has no `attrib`
                    # record.
                    current_name=slot.first_player,
                    display_name=MVP_TEAM_ORDER[slot.index],
                )
                for slot in info.team_slots
            ],
            extra={
                # PROVEN EQUIVALENT under mutation: `len(reader.records)` is the
                # same number for any state reachable from here, because
                # `parse_all` fills `records` for exactly the names
                # `decompress_all` put in `sections` and nothing else on this
                # path adds to either. `sections` is the one asked for because
                # it is the count of streams that decompressed, which is what a
                # user comparing two discs wants.
                "sections_read": len(reader.sections),
                "attrib_records": len(reader.records.get("attrib", {})),
            },
        )

    # -- fetch --------------------------------------------------------------

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
            # DELIBERATE DIVERGENCE: the source called `get_baseball_squad(team.id)`
            # with no season while passing one to the leaders call, and took
            # `season: int = 2025` as a default rather than requiring it. The
            # squad endpoint has no season in its URL but does have one in its
            # cache key, so without it the first season ever fetched was served
            # for every later season on the same machine.
            players = self.api.get_baseball_squad(team.id, season)
            leaders = self.api.get_baseball_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # DELIBERATE DIVERGENCE: the source left these on
                    # `self.team_stats`, an instance attribute created by
                    # `fetch_rosters` and read back through
                    # `getattr(self, "team_stats", {})`. `fetch_rosters`
                    # returned only `dict[str, list[Player]]`, so the statistics
                    # had no way to travel with the rosters at all, and the
                    # pygame front end smuggled them between two separate
                    # patcher instances by hand -- `app.py:11414` copies them off
                    # the fetching instance and `app.py:11490` assigns them onto
                    # the patching one. Without that line every player silently
                    # took position defaults. In `extra` the whole result
                    # round-trips through JSON with the rosters.
                    extra={"leaders": leaders or {}},
                )
            )

        if on_progress is not None:
            on_progress(1.0, "Complete")
        # Every field but `season` is synthesised: this game has no league
        # endpoint. `country` and `country_code` are distinct fields, and
        # `teams_count` counts the rosters actually built -- the slot-mapped
        # subset -- not `len(teams)`.
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

    # -- map ----------------------------------------------------------------

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to a list of `MVPPlayerRecord` per matched slot.

        Sparse: a key exists only for a slot some fetched team mapped to. Each
        value is up to 25 records in ROM slot order -- fifteen batters, five
        starters, five relievers -- with `roster_position` and `batting_order`
        already assigned from the slot index.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, list[MVPPlayerRecord]] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            # The range half of this is PROVEN EQUIVALENT under mutation, and
            # kept. `get_team_slot` answers `MVP_ABBREV_TO_INDEX.get(...)`, and
            # that dict is built by enumerating the thirty entries of
            # `MVP_TEAM_ABBREVS`, so a non-None slot is already in `[0, 30)`.
            # Kept because `_write_all_teams` has to make the same test for real
            # -- its keys come from a plain dict that may have crossed a JSON
            # boundary -- and the two reading alike is worth more than deleting
            # the one that cannot fire.
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue

            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders)
            records = [
                self._map_one(player, leaders.get(str(player.id), {}), index)
                for index, player in enumerate(selected)
            ]

            # DELIBERATE DIVERGENCE. `MODERN_MLB_TO_MVP` collapses 32 provider
            # codes onto 30 slots -- `OAK`/`ATH` and `CWS`/`CHW` -- so two
            # entries in `data.teams` can name one slot. The source assigned
            # `teams[slot].players = mvp_players` unconditionally. It got away
            # with it because its own `fetch_rosters` kept a dict keyed by team
            # code and stored a team only `if players:`, so an empty roster
            # could never reach the mapping step; that protection lasts exactly
            # until someone calls `map_rosters` on a rosters file. Without this
            # guard an empty alias arriving second wipes the populated record
            # and the run reports success with that club's 2005 roster intact.
            if not records and teams.get(slot):
                continue
            teams[slot] = records
        return MappedRosters(game_id=self.game_id, teams=teams)

    def _map_one(
        self,
        player: Player,
        stats: dict[str, Any],
        index: int,
    ) -> MVPPlayerRecord:
        """Map one selected player, given his position in the 25-slot order.

        Whether he is mapped as a pitcher comes from his provider position, and
        whether he is mapped as a *starting* pitcher comes from his slot -- 15
        to 19 is the rotation. Those two can disagree: a squad of nine batters
        and no pitchers puts a batter in slot 15, and he is still mapped as a
        batter. That is the source's behaviour.
        """
        is_starter = BATTERS_PER_TEAM <= index < BATTERS_PER_TEAM + STARTERS_PER_TEAM
        if self.mapper.is_pitcher(player):
            record = self.mapper.map_pitcher(player, stats, is_starter=is_starter)
        else:
            record = self.mapper.map_batter(player, stats)
        record.roster_position = self._slot_to_position(index)
        record.batting_order = index + 1 if index < len(LINEUP_POSITIONS) else NOT_IN_LINEUP
        return record

    @staticmethod
    def _slot_to_position(slot: int) -> str:
        """The game's position string for one of the 25 roster slots.

        Slots 0-8 are the batting order, 9-14 the bench, 15-19 the rotation and
        20-24 the bullpen. A slot past 24 -- which `select_roster` cannot
        produce, since it returns at most 25 -- takes the last bullpen role, the
        source's `min(slot - 20, 4)`.
        """
        if slot < len(LINEUP_POSITIONS):
            return LINEUP_POSITIONS[slot]
        if slot < BATTERS_PER_TEAM:
            return BENCH_POSITION
        if slot < BATTERS_PER_TEAM + STARTERS_PER_TEAM:
            return ROTATION_POSITIONS[slot - BATTERS_PER_TEAM]
        bullpen_slot = slot - (BATTERS_PER_TEAM + STARTERS_PER_TEAM)
        return BULLPEN_POSITIONS[min(bullpen_slot, len(BULLPEN_POSITIONS) - 1)]

    # -- patch --------------------------------------------------------------

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
        # First, ahead of every other guard and ahead of the first status
        # message: it is the one check that costs no I/O, and the failure it
        # prevents is the writer choking on another game's record type with an
        # exception outside this library's hierarchy.
        rosters.require_game(self.game_id)

        with as_rom_error(rom_path):
            self.status("Validating ISO...")
            # The arithmetic bound, and NOT `validate_deep`: see `analyze_rom`
            # for why only one of the two reaches this method.
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

            self.status("Writing rosters...")
            teams_patched, players_patched = self._write_all_teams(writer, rosters, on_progress)

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

    # -- patch helpers ------------------------------------------------------

    def _write_all_teams(
        self,
        writer: MVPPSPRomWriter,
        rosters: MappedRosters,
        on_progress: ProgressFn | None,
    ) -> tuple[int, int]:
        """Stage every mapped slot's records, returning (teams, players) written.

        The slot range is re-checked here as well as in `map_rosters`, because
        the keys come from a plain dict that may have crossed a JSON boundary
        since.

        The `roster` table is rebuilt rather than merged: every row belonging to
        a team being patched is dropped and replaced, so the club ends up with
        exactly the players it was given and none of its 2005 squad. Rows for
        teams *not* being patched are kept untouched, which is what lets a user
        patch one division and leave the rest of the league alone.
        """
        targets = sorted(
            slot for slot, players in rosters.teams.items() if 0 <= slot < TEAM_COUNT and players
        )
        pool = _HashPool(
            list(writer.reader.records.get("attrib", {})),
            set(writer.reader.records.get("pitchattrib", {})),
        )

        patched_team_hashes = {TEAM_HASHES[MVP_TEAM_ABBREVS[slot]] for slot in targets}
        old_roster = writer.reader.records.get("roster", {})
        new_roster: Table = {
            rec_id: fields
            for rec_id, fields in old_roster.items()
            if fields.get(ROSTER_TEAMID, "") not in patched_team_hashes
        }
        # New roster row ids continue past the highest the disc already had, so
        # they cannot collide with a preserved row. The source computed this
        # from `old_roster` -- every row, not just the preserved ones -- and
        # that is kept: a dropped row's id is still an id the disc used, and
        # reusing it would give the new row that row's history in any table
        # keyed on it.
        roster_counter = max((int(rid, 16) for rid in old_roster), default=0) + 1

        teams_patched = 0
        players_patched = 0
        for i, slot in enumerate(targets):
            players: list[MVPPlayerRecord] = rosters.teams[slot]
            if on_progress is not None:
                on_progress(
                    (i / len(targets)) * PROGRESS_RECORDS_END,
                    f"Writing {MVP_TEAM_ORDER[slot]} ({len(players)} players)...",
                )
            team_hash = TEAM_HASHES[MVP_TEAM_ABBREVS[slot]]
            for player_index, player in enumerate(players):
                player.hash_id = pool.take(
                    is_pitcher=player.is_pitcher,
                    team_index=slot,
                    player_index=player_index,
                )
                self._write_player(writer, player)
                new_roster[f"{roster_counter:0{HASH_ID_CHARS}x}"] = self._build_roster_fields(
                    team_hash, player, slot
                )
                roster_counter += 1
                players_patched += 1
            teams_patched += 1

        writer.update_records("roster", new_roster)
        if pool.crossed or pool.synthesised:
            # The source degraded here in silence. Saying so is the whole of
            # the change; the degradation itself is preserved, because every
            # alternative drops a player.
            self.status(
                f"{pool.crossed} player(s) took an id from the other position pool and "
                f"{pool.synthesised} were given a synthesised id; their career statistics "
                f"in this game will not be their own"
            )
        return teams_patched, players_patched

    def _write_player(self, writer: MVPPSPRomWriter, player: MVPPlayerRecord) -> None:
        """Stage one player's four table records.

        Three tables for a batter and four for a pitcher: `pitchattrib` gets a
        row only for a pitcher, so a batter given a pitcher's recycled id keeps
        that pitcher's arsenal in the table. That is inherited, and it is the
        mirror of the `_HashPool` cross-fall.
        """
        writer.update_player_record("attrib", player.hash_id, self._build_attrib_fields(player))
        writer.update_player_record(
            "lrattrib_rhp", player.hash_id, self._build_lr_attrib_fields(player, vs_rhp=True)
        )
        writer.update_player_record(
            "lrattrib_lhp", player.hash_id, self._build_lr_attrib_fields(player, vs_rhp=False)
        )
        if player.is_pitcher:
            writer.update_player_record(
                "pitchattrib", player.hash_id, self._build_pitchattrib_fields(player)
            )

    @staticmethod
    def _build_attrib_fields(player: MVPPlayerRecord) -> dict[int, str]:
        """The `attrib` columns this patcher sets. Everything else is merged through.

        UPSTREAM BEHAVIOUR, KNOWN WRONG, PRESERVED DELIBERATELY -- **height is
        written unconditionally**, column 9, from a `MVPPlayerRecord.height`
        that nothing ever sets. It keeps its default of 72, so all 750 patched
        players are written at exactly 6'0", over whatever heights the disc
        knew. `sports.models.Player` has no `height` field at all, for any
        provider to fill, so there is nothing better to write and never was.

        Dropping the column would leave the disc's own per-player values, which
        is strictly more information. It is not dropped: this port's output has
        never been checked against a retail UMD, and a byte the source did not
        write is a hardware risk that a truer biography does not buy off.
        `games/nhl05_ps2` and `games/nhl07_psp` write the same constant into
        `HEIG` for the same reason.

        UPSTREAM BEHAVIOUR, KNOWN WRONG, PRESERVED DELIBERATELY -- **weight is
        written unconditionally too**, column 10, from a
        `MVPPlayerRecord.weight` that nothing sets, so every patched player
        weighs 190 lb and the disc's own weights go the way its heights do.
        `Player.weight` is filled -- `sports/espn.py`'s `_parse_baseball_squad`
        reads the MLB roster endpoint's own figure into it -- and `map_batter`
        and `map_pitcher` do not read it. Same argument as the height above: an
        unverified byte is the risk, not a stale number.

        The secondary position is written only when the mapper produced one,
        which it never does today -- `MVPPlayerRecord.secondary_position` has no
        producer. Kept because it is the merge that makes it matter: writing an
        empty string would erase the disc's own second position and leave the
        player unable to be moved in the field.
        """
        fields = {
            ATTRIB_FIRST_NAME: player.first_name,
            ATTRIB_LAST_NAME: player.last_name,
            ATTRIB_JERSEY: str(player.jersey),
            ATTRIB_BATS: str(player.bats),
            ATTRIB_THROWS: str(player.throws),
            ATTRIB_PRIMARY_POS: str(
                POS_STRING_TO_NUM.get(player.primary_position, DEFAULT_POS_NUM)
            ),
            ATTRIB_HEIGHT: str(player.height),
            ATTRIB_WEIGHT: str(player.weight),
            ATTRIB_PLATE_DISCIPLINE: str(player.plate_discipline),
            ATTRIB_BUNTING: str(player.bunting),
            ATTRIB_STEALING_AGGRESSIVE: str(player.stealing),
            ATTRIB_BASERUNNING: str(player.baserunning),
            ATTRIB_SPEED: str(player.speed),
            ATTRIB_FIELDING: str(player.fielding),
            ATTRIB_RANGE: str(player.arm_range),
            ATTRIB_THROW_STRENGTH: str(player.throw_strength),
            ATTRIB_THROW_ACCURACY: str(player.throw_accuracy),
            ATTRIB_DURABILITY: str(player.durability),
            ATTRIB_STARPOWER: str(player.starpower),
        }
        if player.secondary_position:
            fields[ATTRIB_SECONDARY_POS] = str(POS_STRING_TO_NUM.get(player.secondary_position, 0))
        return fields

    @staticmethod
    def _build_lr_attrib_fields(player: MVPPlayerRecord, *, vs_rhp: bool) -> dict[int, str]:
        """Name, contact and power in one of the two split tables.

        The spray chart, the batted-ball mix and the outfield fielding
        percentages are not written, so they stay as the disc has them for the
        player whose id is being reused. There is nothing in any provider's data
        that could produce them.
        """
        contact = player.contact_rhp if vs_rhp else player.contact_lhp
        power = player.power_rhp if vs_rhp else player.power_lhp
        return {
            LR_FIRST_NAME: player.first_name,
            LR_LAST_NAME: player.last_name,
            LR_CONTACT: str(contact),
            LR_POWER: str(power),
        }

    @staticmethod
    def _build_pitchattrib_fields(player: MVPPlayerRecord) -> dict[int, str]:
        """Stamina, pickoff and up to four pitches.

        Pitch 1 is the asymmetric one: always a fastball, so it has no type
        column and occupies four columns where each later pitch occupies five.
        Pitches 2-4 go into the repeating block; **pitch 5 is never written**,
        because the source sliced `pitches[1:4]` and the mapper produces at most
        three pitches anyway. The description column of each pitch is left
        alone -- it is a display string this patcher has no source for.
        """
        fields: dict[int, str] = {
            PA_FIRST_NAME: player.first_name,
            PA_LAST_NAME: player.last_name,
            PA_STAMINA: str(player.stamina),
            PA_PICKOFF: str(player.pickoff),
        }
        if player.pitches:
            first = player.pitches[0]
            fields[PA_PITCH1_MOVEMENT] = str(first.movement)
            fields[PA_PITCH1_CONTROL] = str(first.control)
            fields[PA_PITCH1_VELOCITY] = str(first.velocity)

        for i, pitch in enumerate(player.pitches[1 : 1 + MAX_EXTRA_PITCHES]):
            base = PA_PITCH2_TYPE + i * PA_PITCH_STRIDE
            fields[base + PA_PITCH_TYPE_OFFSET] = str(pitch.type)
            fields[base + PA_PITCH_MOVEMENT_OFFSET] = str(pitch.movement)
            fields[base + PA_PITCH_CONTROL_OFFSET] = str(pitch.control)
            fields[base + PA_PITCH_VELOCITY_OFFSET] = str(pitch.velocity)
        return fields

    @staticmethod
    def _build_roster_fields(
        team_hash: str,
        player: MVPPlayerRecord,
        slot: int,
    ) -> dict[int, str]:
        """One `roster` row: the team, the player, and four lineup assignments.

        The game keeps a separate lineup for each combination of the opposing
        starter's handedness and the host league's designated-hitter rule, so
        four (position, batting order) pairs. The position is the same in all
        four; only the batting order differs, and it is set in the pair
        matching this club's league and -1 in the other.

        Which league that is comes from the slot index and `AL_SLOT_COUNT`:
        `MVP_TEAM_ABBREVS` is ordered fourteen American League clubs then
        sixteen National, which were the 2005 league sizes. The source wrote
        `team_index < 14` inline with no explanation.

        The two handedness variants are given the same lineup, which means the
        game's platoon feature does nothing for a patched team. Inherited, and
        it needs a per-handedness split from the provider that nothing here has.
        """
        pos = player.roster_position
        order = str(player.batting_order)
        al_order = order if slot < AL_SLOT_COUNT else str(NOT_IN_LINEUP)
        nl_order = str(NOT_IN_LINEUP) if slot < AL_SLOT_COUNT else order
        return {
            ROSTER_TEAMID: team_hash,
            ROSTER_PLAYERID: player.hash_id,
            ROSTER_RH_AL_POS: pos,
            ROSTER_RH_AL_ORDER: al_order,
            ROSTER_RH_NL_POS: pos,
            ROSTER_RH_NL_ORDER: nl_order,
            ROSTER_LH_AL_POS: pos,
            ROSTER_LH_AL_ORDER: al_order,
            ROSTER_LH_NL_POS: pos,
            ROSTER_LH_NL_ORDER: nl_order,
        }
