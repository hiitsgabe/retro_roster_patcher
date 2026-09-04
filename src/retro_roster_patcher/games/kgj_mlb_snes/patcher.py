"""Ken Griffey Jr. Presents Major League Baseball (SNES) on the unified Patcher interface.

The translation layer between the ported reader/writer/mapper -- a faithful copy
of an untested upstream, and kept that way -- and the contracts in
`core.patcher`. Where the ported code breaks one of those contracts it is worked
around here rather than fixed there.

Five things about this game are worth knowing before reading the code.

**The team tables are found by searching, not by an offset.**
`KGJRomReader.validate` runs `data.find(FIRST_TEAM_MARKER)` over the whole image
and records the byte just past the 14-byte match; every player offset in the
package is relative to that. This is why nothing here does headered/headerless
offset arithmetic: a 512-byte copier header moves the marker by 512 and the
search finds it 512 bytes later, so the recorded offset is already a file
offset. `rom_writer.update_snes_checksum` is the single exception, and only
because the SNES header it edits is at a fixed address.

**Nothing bounds where that search may land.** `validate` accepts the match
wherever it is, and 25 280 bytes of team data follow it. If the marker turns up
within 25 280 bytes of the end -- which `validate` permits -- every read past
the end answers `{}` and every write answers `False`, and upstream's `patch_rom`
then returned `success=True` with `teams_patched=0`. `_team_data_fits` is the
guard, and it is the only structural check this port adds; see its docstring for
why the signature needs no more than that.

**The character encoding has one lowercase letter.** `models.CHAR_TO_BYTE`
covers space, the digits, A-Z and a lone `c` at 0x36 so "McGWIRE" renders.
Everything else -- every accent in a modern MLB roster, every apostrophe --
encodes to 0x00, a SPACE, silently. Last names are also hard-truncated to eight
characters and first names reduced to one initial. So "José Ramírez" reaches the
cartridge as "J. RAM REZ". That is upstream's behaviour and it is preserved;
fixing it means extending the table against a real font, which no test in this
repository can check.

**The SNES checksum IS recomputed, and by `patch` rather than by `finalize`.**
`update_snes_checksum` writes the 16-bit sum of every byte at 0x7FDE and its
complement at 0x7FDC, both shifted by 512 on a headered image, and `patch` calls
it explicitly before `finalize`. The NHL 94 SNES port in this library does none
of this, and the NBA Live 95 port does its equivalent *inside* `finalize`. All
three are deliberate: each is where its own upstream put it. Do not harmonise
them.

**`RomSlot.current_name` carries a player, and says so.** The upstream slot
record is `(index, name, first_player)`: `name` is `KGJ_TEAM_ORDER[i]`, a
constant, and `first_player` is the only part actually read from the image. This
reader never parses a team-name string -- the ROM's team names are not in
`models.py` at all -- so there is no ROM-derived team name to put in
`current_name`. The same problem, and the same answer, as the NBA Live 95 port:
`analyze_rom` writes `"First player: K. GRIFFEY"`, and an empty string where no
record could be read, while `display_name` takes the constant, which is what it
is for and is distinct across all 28 slots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.errors import ApiError, RomError
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
from ...sports.models import League, LeagueData, TeamRoster
from .models import (
    BATTERS_PER_TEAM,
    KGJ_TEAM_ORDER,
    ROSTER_TYPE_BATTER,
    ROSTER_TYPE_RELIEVER,
    ROSTER_TYPE_STARTER,
    STARTERS_PER_TEAM,
    TEAM_COUNT,
    KGJPlayerRecord,
    KGJTeamRecord,
)
from .rom_reader import TEAM_DATA_SPAN, KGJRomReader
from .rom_writer import KGJRomWriter
from .stat_mapper import KGJStatMapper


def _team_data_fits(reader: KGJRomReader) -> bool:
    """Do all 28 team blocks lie inside the file, given where the marker was found?

    IMPROVEMENT, with no upstream equivalent. `KGJRomReader.validate` requires
    the file to be exactly 2 097 152 bytes or exactly that plus a 512-byte
    copier header, and then takes `data.find(FIRST_TEAM_MARKER)` wherever it
    lands. Those two facts do not combine into a bound: the marker may match at
    offset 2 090 000 of a correctly-sized file, and then `get_team_offset` hands
    out addresses past the end for most of the league.

    Nothing crashes when that happens, which is the problem. `read_player`
    answers `{}` and `write_player` answers `False`, so `write_team_roster`
    returns 0, `teams_patched` never increments, and upstream's `patch_rom`
    returned `success=True` having written a byte-for-byte copy of the input.

    The condition is `write_player`'s own -- `off + PLAYER_LENGTH >
    len(self.data)` -- evaluated for the last of the 700 records rather than for
    each in turn. `TEAM_DATA_SPAN` is derived in `rom_reader` from
    `get_team_offset`'s arithmetic, not transcribed.

    Unlike a content heuristic this is arithmetic, not a guess about a real
    image: a file that fails it cannot be patched at all, so `patch` refuses it
    rather than writing an unmodified copy under a success return. `analyze_rom`
    reports the same file as `is_valid=False`.
    """
    if reader.data is None:
        return False
    return reader.first_team_offset + TEAM_DATA_SPAN <= len(reader.data)


def _roster_type_for_slot(slot: int) -> int:
    """Which roster-type nibble a record in this slot carries.

    The single home of a fact upstream encoded twice, in two files, one of them
    as a magic number. `KGJRomWriter.write_team_roster` derived it from
    `BATTERS_PER_TEAM` and `STARTERS_PER_TEAM` while mutating the caller's
    records; `patcher.py` separately wrote `is_starter = idx < 20`, hardcoding
    the sum of those two constants. Both now come from here.
    """
    if slot < BATTERS_PER_TEAM:
        return ROSTER_TYPE_BATTER
    if slot < BATTERS_PER_TEAM + STARTERS_PER_TEAM:
        return ROSTER_TYPE_STARTER
    return ROSTER_TYPE_RELIEVER


@register(
    "kgj-mlb-snes",
    platform="snes",
    sport="baseball",
    requires_slot_mapping=False,
    providers=("espn",),
)
class KGJMLBPatcher(Patcher):
    """Teams map to ROM slots by abbreviation, so no manual mapping step.

    28 slots, all of them patchable: 14 AL then 14 NL, in the 1994 league order.
    `MODERN_MLB_TO_KGJ` maps 30 modern abbreviations onto them, so Arizona and
    Tampa Bay -- neither of which existed in 1994 -- have no slot and are
    dropped before any request goes out.

    ESPN is the only provider, and this is the first baseball game in the
    library: `EspnClient.get_mlb_teams`, `get_baseball_squad` and
    `get_baseball_team_leaders` existed and were unreachable until this patcher
    was registered. The squad endpoint is where `Player.bats` and
    `Player.handedness` come from, and the mapper needs both -- `bats` for the
    batting stance byte and `handedness` for a pitcher's throwing hand.
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
        self.mapper = KGJStatMapper()
        # Built eagerly, and `EspnClient.__init__` creates its cache directory,
        # so constructing this patcher can raise `StorageError`. Nothing here
        # reaches the network.
        self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = KGJRomReader(str(rom_path))
        if not reader.load():
            # `load` catches its own OSError and answers False, so a missing
            # file, a revoked read bit and an EIO all arrive here as the same
            # False. That is the one case `analyze_rom` may raise for.
            #
            # DELIBERATE DIVERGENCE. Upstream returned
            # `KGJRomInfo(path=rom_path, size=0)` here -- a size of 0 for a file
            # that may be 2 MB, and `is_valid=False`, which is the same answer it
            # gives for a readable image of a different game. The library needs
            # those two apart: `cmd_analyze` catches `RomError` per patcher and
            # continues, and treats `is_valid=False` as a considered "not this
            # game".
            raise RomError(f"Cannot read ROM: {rom_path}")
        info = reader.get_info()
        is_valid = info.is_valid and _team_data_fits(reader)

        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    # Labelled, because it is a player and not a team. See this
                    # module's docstring.
                    current_name=(
                        f"First player: {slot.first_player}" if slot.first_player else ""
                    ),
                    display_name=slot.name,
                )
                for slot in info.team_slots
            ],
            # Both are ROM-derived and neither is reachable any other way once
            # the reader is gone: `has_header` decides which end of the file the
            # checksum lands at, and `first_team_offset` is the only evidence of
            # where the marker actually matched. JSON-serialisable, per
            # `core/models.py`.
            extra={
                "has_header": info.has_header,
                "first_team_offset": info.first_team_offset,
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
        self.status("Fetching MLB teams...")
        teams = self.api.get_mlb_teams()
        if not teams:
            raise ApiError("The provider returned no MLB teams")

        # Only teams that exist as a slot in the 1994 ROM are worth fetching:
        # Arizona and Tampa Bay cost two network round trips each and are then
        # discarded by `map_rosters`.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches a Ken Griffey Jr. MLB ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            # DELIBERATE DIVERGENCE: upstream called `get_baseball_squad(team.id)`
            # with no season at all. The squad endpoint has no season in its URL
            # but does have one in its cache key, so without it the first season
            # ever fetched was served forever. The leaders endpoint takes the
            # season as a path segment, and upstream did pass it there.
            players = self.api.get_baseball_squad(team.id, season)
            leaders = self.api.get_baseball_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # Upstream left these on `self.team_stats`, an instance side
                    # channel between `fetch_rosters` and `map_rosters_to_kgj`
                    # that no serialised rosters file could carry -- and that
                    # `map_rosters_to_kgj` reached for with `getattr(self,
                    # "team_stats", {})`, so calling the two out of order was a
                    # silent downgrade to position defaults rather than an error.
                    # In `extra` the whole result round-trips through JSON and
                    # the two steps can run in separate processes.
                    extra={"leaders": leaders or {}},
                )
            )

        if on_progress is not None:
            on_progress(1.0, "Complete")
        # Every field but `season` is synthesised: this game has no league
        # endpoint. `country` and `country_code` are distinct fields -- "USA"
        # and "US" -- and `teams_count` counts the rosters actually built, the
        # slot-mapped subset of what the provider returned, not `len(teams)`.
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
        """Reduce league data to one `KGJTeamRecord` per matched ROM slot.

        Sparse: a key exists only for a slot some fetched team mapped to, where
        upstream always built all 28 records and left the unmatched ones empty.
        Nothing reads an empty record, and `patch` skips it either way.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, KGJTeamRecord] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            # `0 <=` and not just `< TEAM_COUNT`: no value in `MODERN_MLB_TO_KGJ`
            # is negative today, so this half of the bound is a guard and not a
            # filter, and `tests/games/kgj_mlb_snes/test_patcher.py` reaches it
            # with a stub mapper rather than leaving it unexercised.
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders)

            records: list[KGJPlayerRecord] = []
            for index, player in enumerate(selected):
                player_stats = leaders.get(str(player.id), {})
                # `is_starter` from the slot boundaries rather than upstream's
                # `idx < 20`, which hardcoded `BATTERS_PER_TEAM +
                # STARTERS_PER_TEAM`. Same value, one source.
                is_starter = index < BATTERS_PER_TEAM + STARTERS_PER_TEAM
                if self.mapper.is_pitcher(player):
                    record = self.mapper.map_pitcher(
                        player,
                        player_stats,
                        is_starter=is_starter,
                    )
                else:
                    record = self.mapper.map_batter(player, player_stats)
                # Stamped here, where the record is built. Upstream's
                # `KGJRomWriter.write_team_roster` did it, mutating records the
                # caller still held; see the DELIBERATE DIVERGENCE note there.
                record.roster_type = _roster_type_for_slot(index)
                records.append(record)

            # `MODERN_MLB_TO_KGJ` maps 30 codes onto 28 slots: CWS/CHW both name
            # slot 3 and OAK/ATH both name slot 10, so two entries in
            # `data.teams` can target the same one. Upstream kept its rosters in
            # a dict keyed by team code and only stored a team whose squad was
            # non-empty, so an empty alias could never displace a populated one;
            # here the slot is assigned directly. Without this guard an empty
            # alias arriving second would wipe the populated record, `patch`
            # would skip the slot, and the run would report success with
            # `teams_patched` short by one and the 1994 roster still in place.
            #
            # An empty roster that collides with nothing still takes the slot:
            # the mapped result keeps showing which slots a provider team
            # matched, and `patch` is what keeps the empty list away from the
            # writer.
            existing = teams.get(slot)
            if not records and existing is not None and existing.players:
                continue
            teams[slot] = KGJTeamRecord(
                index=slot,
                name=KGJ_TEAM_ORDER[slot],
                players=records,
            )
        return MappedRosters(game_id=self.game_id, teams=teams)

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
        # First, ahead of every other guard and ahead of the first status
        # message: it is the one check that costs no I/O, and the failure it
        # prevents is the writer choking on another game's record type with an
        # exception outside this library's hierarchy.
        rosters.require_game(self.game_id)
        self.status("Validating ROM...")
        reader = KGJRomReader(str(rom_path))
        if not reader.load() or not reader.validate():
            raise RomError(f"Not a valid Ken Griffey Jr. MLB ROM: {rom_path}")
        if not _team_data_fits(reader):
            size = len(reader.data or b"")
            raise RomError(
                f"Not a valid Ken Griffey Jr. MLB ROM: {rom_path}: the team tables start at "
                f"{reader.first_team_offset:#x} and run {TEAM_DATA_SPAN} bytes, past the end of a "
                f"{size}-byte file"
            )

        self.status("Initializing ROM writer...")
        # The image is read from disk twice -- once above, once by the writer's
        # own internal reader -- so one whole copy of the file is redundant I/O
        # per patch. Kept deliberately: it is what lets "not this game" fail
        # before any writer state exists, and the writer owns its reader for its
        # whole lifetime.
        writer = KGJRomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # `MappedRosters.filled_slots()` is unusable here and its docstring in
        # `core/models.py` says why: this game's mapped value is an object, so
        # every one of them is truthy however empty and it would return every
        # key. The emptiness that matters is the player list's.
        #
        # The range is re-checked because those keys come from a plain dict that
        # may have crossed a JSON boundary since `map_rosters` built it.
        # `write_team_roster` guards only `team_index >= TEAM_COUNT`, so a
        # negative key would reach `get_team_offset`, compute an offset below
        # the marker, and overwrite whatever the ROM keeps there.
        targets = sorted(
            slot for slot, team in rosters.teams.items() if 0 <= slot < TEAM_COUNT and team.players
        )

        teams_patched = 0
        players_patched = 0
        for index, slot in enumerate(targets):
            if on_progress is not None:
                on_progress(index / len(targets), f"Writing {KGJ_TEAM_ORDER[slot]}...")
            team: KGJTeamRecord = rosters.teams[slot]
            written = writer.write_team_roster(slot, team.players)
            if written <= 0:
                # -1 is the writer's error return and 0 means not one of the 25
                # records was written. Either way nothing reached the image, so
                # nothing is counted.
                continue
            teams_patched += 1
            # `written`, not `len(team.players)`: `write_player` answers False
            # for a record that would run off the end of the file and the loop
            # carries on, so the two numbers can differ. `core/models.py`
            # defines `players_patched` as records that reached the image.
            players_patched += written

        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")

        # Explicitly, here, and not inside `finalize`. See this module's
        # docstring: the NBA Live 95 port does the opposite and both are
        # deliberate.
        writer.update_snes_checksum()

        self.status("Saving patched ROM...")
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
