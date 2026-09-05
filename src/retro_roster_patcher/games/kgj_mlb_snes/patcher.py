"""Ken Griffey Jr. Presents Major League Baseball (SNES) on the unified Patcher interface.

The team tables are found by searching for a 14-byte marker, not by a fixed
offset, so a 512-byte copier header needs no arithmetic anywhere in this package:
the search simply finds the marker 512 bytes later and the recorded offset is
already a file offset. `rom_writer.update_snes_checksum` is the one exception,
because the SNES header it edits sits at a fixed address.

The reader accepts that match wherever it lands, and 25 280 bytes of team data
follow it. `_team_data_fits` is the bound.

The SNES checksum is recomputed by `patch`, not by `finalize`, which is where
this game's upstream put it; the NBA Live 95 port does its equivalent inside
`finalize`. Do not harmonise them.

The ROM's team names are never parsed, so `RomSlot.current_name` carries the
first player read out of the slot, labelled as such, and `display_name` takes the
`KGJ_TEAM_ORDER` constant.
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

    A correctly-sized image can still match `FIRST_TEAM_MARKER` near its end, and
    then `get_team_offset` hands out addresses past the end for most of the
    league. Nothing crashes: reads answer `{}`, writes answer `False`, and the
    patch reports success having copied the input unchanged. This is
    `write_player`'s own bound -- `off + PLAYER_LENGTH > len(self.data)` --
    evaluated once for the last of the 700 records.
    """
    if reader.data is None:
        return False
    return reader.first_team_offset + TEAM_DATA_SPAN <= len(reader.data)


def _roster_type_for_slot(slot: int) -> int:
    """Which roster-type nibble (record byte 0x19, high half) a slot gets.

    Upstream's behaviour, known wrong, preserved for byte fidelity: the slot
    index is right only while all three groups are full. On a short roster a
    starting pitcher can land in a batter slot and be stamped
    `ROSTER_TYPE_BATTER` while `write_player` lays down a pitcher-shaped record,
    so byte 0x19 and byte 0x1D contradict each other. Do not derive the nibble
    from the mapper's groups instead; `map_rosters`'s `is_starter = index < 20`
    is the same boundary written a second way and the two are kept in step.
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

    28 slots, all patchable: 14 AL then 14 NL, in the 1994 league order.
    `MODERN_MLB_TO_KGJ` maps 30 modern abbreviations onto them, so Arizona and
    Tampa Bay -- neither of which existed in 1994 -- have no slot and are dropped
    before any request goes out.
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
        self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = KGJRomReader(str(rom_path))
        if not reader.load():
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
                    # Labelled, because it is a player and not a team.
                    current_name=(
                        f"First player: {slot.first_player}" if slot.first_player else ""
                    ),
                    display_name=slot.name,
                )
                for slot in info.team_slots
            ],
            # `has_header` decides which end of the file the checksum lands at,
            # and `first_team_offset` records where the marker matched.
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

        # Only teams that exist as a slot in the 1994 ROM are worth fetching.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches a Ken Griffey Jr. MLB ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            # The squad endpoint has no season in its URL but does have one in
            # its cache key, so the season must be passed here too or the first
            # season ever fetched is served forever.
            players = self.api.get_baseball_squad(team.id, season)
            leaders = self.api.get_baseball_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    extra={"leaders": leaders or {}},
                )
            )

        if on_progress is not None:
            on_progress(1.0, "Complete")
        # Every field but `season` is synthesised: this game has no league
        # endpoint. `teams_count` counts the rosters actually built, the
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

    def suggest_squad_order(self, team_roster):
        leaders = team_roster.extra.get("leaders") or {}
        batters, starters, relievers = self.mapper.select_roster_groups(
            team_roster.players, leaders
        )
        return self._append_unused(batters + starters + relievers, team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to one `KGJTeamRecord` per matched ROM slot.

        Sparse: a key exists only for a slot some fetched team mapped to.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, KGJTeamRecord] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders)

            records: list[KGJPlayerRecord] = []
            for index, player in enumerate(selected):
                player_stats = leaders.get(str(player.id), {})
                # Upstream's literal, equal to `BATTERS_PER_TEAM +
                # STARTERS_PER_TEAM`; see `_roster_type_for_slot`.
                is_starter = index < 20  # slots 15-19 are starters
                if self.mapper.is_pitcher(player):
                    record = self.mapper.map_pitcher(
                        player,
                        player_stats,
                        is_starter=is_starter,
                    )
                else:
                    record = self.mapper.map_batter(player, player_stats)
                record.roster_type = _roster_type_for_slot(index)
                records.append(record)

            # `MODERN_MLB_TO_KGJ` maps 30 codes onto 28 slots: CWS/CHW both name
            # slot 3 and OAK/ATH both name slot 10, so two entries in
            # `data.teams` can target the same one and an empty alias arriving
            # second must not wipe a populated record.
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
        writer = KGJRomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # `write_team_roster` guards only `team_index >= TEAM_COUNT`, so a
        # negative key would reach `get_team_offset`, compute an offset below the
        # marker, and overwrite whatever the ROM keeps there.
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
                # -1 is the writer's error return, 0 means no record was written.
                continue
            teams_patched += 1
            # `written`, not `len(team.players)`: `write_player` answers False for
            # a record that would run off the end of the file.
            players_patched += written

        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")

        # Explicitly here, not inside `finalize`. See this module's docstring.
        writer.update_snes_checksum()

        self.status("Saving patched ROM...")
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
