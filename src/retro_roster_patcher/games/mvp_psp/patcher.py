"""MVP Baseball (PSP) on the unified Patcher interface.

It patches CSV text inside compressed sections, not byte offsets and not
bit-packed TDB records: a write changes a column of a line of ASCII, then the
whole table is re-serialised, recompressed with RefPack and put back in a
fixed-size hole. A wrong column number writes a real column with the wrong
meaning, and a table that compresses worse after the edit than before cannot be
stored at all (`rom_writer.SectionTooLargeError`).

Player ids are recycled from the disc, not invented. MVP's tables link by
nine-hex-digit ids, and eight tables this patcher never writes -- `batstat`,
`fieldstat`, `careerstats`, `pitchcareer` and the four left/right split-stat
tables -- key on the same ids, so a new player inherits the statistical history
of the id he is given. See `_HashPool`.

`analyze_rom` and `patch` do not apply the same checks, deliberately:
`MVPPSPRomReader.validate_deep` is a heuristic and guards `analyze_rom` only,
`_database_big_extent_fits` is arithmetic and guards both.
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

# Where the roster-writing phase of `patch` ends on the progress bar. The copy
# is the slowest step -- a PSP UMD image is hundreds of megabytes -- but it runs
# last, after every record is staged in memory, so it is only the tail.
PROGRESS_RECORDS_END = 0.9


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


class _HashPool:
    """Hands out the disc's own player ids, pitchers' to pitchers where it can.

    Ids matter because eight tables this patcher never writes key on them --
    `batstat`, `pitchstat`, `fieldstat`, `careerstats`, `pitchcareer` and the
    four split-stat tables. Reusing an id gives the new player the old player's
    entire statistical history, which is why the pools are split: a pitcher
    handed a batter's id gets a batter's career line and appears in the game's
    own leaderboards as a hitter.

    Three tiers, all preserved, because every alternative loses a player:

      1. A pitcher takes the next id from the pitcher pool, a batter from the
         batter pool.
      2. Cross-fall: an exhausted pool borrows from the other, so the player
         inherits the wrong kind of career line. Counted as `crossed`.
      3. Synthesis: both pools are empty and an id is manufactured from the team
         and player index. Counted as `synthesised`.

    A synthesised id is `00`, two hex digits of team index, five of player
    index, then `ff` -- eleven characters, where every id in this repository is
    nine (`models.HASH_ID_CHARS`), so it cannot collide with a real one, and the
    (team, player index) pair is unique within a run so two cannot collide with
    each other. It costs the opposite of a collision: the id matches nothing, so
    the player has no row in any statistics table at all.
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

    `requires_slot_mapping=False` because all 30 slots are real MLB clubs and
    `MODERN_MLB_TO_MVP` covers every one of them, naming no club it has no slot
    for; there is nothing a user could usefully choose. One provider: ESPN is
    the only one in this library with MLB rosters.
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
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # In `extra`, not on the instance: the statistics have to
                    # travel with the rosters through JSON, or a player that
                    # crosses a process boundary takes position defaults.
                    extra={"leaders": leaders or {}},
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
        leaders = team_roster.extra.get("leaders") or {}
        ordered = self.mapper.select_roster(team_roster.players, leaders)
        return self._append_unused(ordered, team_roster.players)

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
            # The range test cannot fire here, and is kept: `_write_all_teams`
            # has to make the same test for real, on keys that may have crossed
            # a JSON boundary, and the two must read alike.
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue

            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders)
            records = [
                self._map_one(player, leaders.get(str(player.id), {}), index)
                for index, player in enumerate(selected)
            ]

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
        index: int,
    ) -> MVPPlayerRecord:
        """Map one selected player, given his position in the 25-slot order.

        Whether he is mapped as a pitcher comes from his provider position, and
        whether he is mapped as a *starting* pitcher comes from his slot -- 15
        to 19 is the rotation. The two can disagree: a squad of nine batters and
        no pitchers puts a batter in slot 15, and he is still mapped as one.
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
        20-24 the bullpen. A slot past 24, which `select_roster` cannot produce,
        takes the last bullpen role.
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
        # Count from `old_roster`, every row and not just the preserved ones: a
        # dropped row's id is still an id the disc used, and reusing it would
        # give the new row that row's history in any table keyed on it.
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
            # Report the degradation; do not fix it. Every alternative to it
            # drops a player. See `_HashPool`.
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
        that pitcher's arsenal in the table. Inherited, and the mirror of the
        `_HashPool` cross-fall.
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

        Upstream behaviour, known wrong, preserved deliberately: height (column
        9) and weight (column 10) are written unconditionally from record fields
        nothing ever sets, so every patched player is written at 6'0" and 190 lb
        over whatever the disc knew. Do not drop the two columns: this port's
        output has never been checked against a retail UMD, and a byte the
        source did not write is the risk, not a stale number.

        The secondary position is written only when the mapper produced one,
        which it never does today. Keep the guard: writing an empty string would
        erase the disc's own second position and leave the player unable to be
        moved in the field.
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
        player whose id is being reused: no provider supplies them.
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
        Pitches 2-4 go into the repeating block and pitch 5 is never written.
        Each pitch's description column is left alone -- it is a display string
        this patcher has no source for.
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
        four; only the batting order differs, set in the pair matching this
        club's league and -1 in the other.

        Which league that is comes from the slot index and `AL_SLOT_COUNT`:
        `MVP_TEAM_ABBREVS` is ordered fourteen American League clubs then
        sixteen National, the 2005 league sizes.

        Inherited: the two handedness variants get the same lineup, so the
        game's platoon feature does nothing for a patched team. Fixing it needs
        a per-handedness split no provider here supplies.
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
