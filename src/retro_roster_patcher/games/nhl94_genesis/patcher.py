"""NHL 94 (Sega Genesis) on the unified Patcher interface."""

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
from ...sports.nhl import NhlApiClient
from .models import NHL94_GEN_TEAM_ORDER, TEAM_COUNT, NHL94GenPlayerRecord
from .rom_reader import NHL94GenesisRomReader
from .rom_writer import NHL94GenesisRomWriter
from .stat_mapper import NHL94GenStatMapper

# Two goalies, four forward lines plus two spares, seven defencemen. A selection
# cap, not a capacity: the writer spends `2 + len(name) + 8` bytes per player
# inside the existing record chain, so a 452-byte region fits roughly 19 of the
# 14-character names `map_player` produces. The overflow is dropped silently.
MAX_PLAYERS_PER_SLOT = 23


@register(
    "nhl94-genesis",
    platform="genesis",
    sport="hockey",
    requires_slot_mapping=False,
    providers=("espn", "nhl"),
)
class NHL94GenesisPatcher(Patcher):
    """Teams map to ROM slots by three-letter code, so no manual mapping step.

    Only the `nhl` provider honours `fetch`'s `season`; ESPN's roster endpoint
    serves the current squad and nothing else.
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
        self.mapper = NHL94GenStatMapper()
        # `Any`: the two clients take different arguments for squad and leaders, so
        # no single type describes both.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = NHL94GenesisRomReader(str(rom_path))
        if not reader.load():
            raise RomError(f"Cannot read ROM: {rom_path}")
        size = len(reader.data or b"")
        try:
            info = reader.get_info()
        except IndexError:
            # `validate` bounds-checks pointer 0 only, so any of the other 25 landing
            # near the end of the file reaches here. Not this game, so report it.
            return RomInfo(
                path=str(rom_path),
                size=size,
                game_id=self.game_id,
                is_valid=False,
            )
        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=info.is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    current_name=slot.current_name,
                    display_name=slot.display_name,
                )
                for slot in info.team_slots
            ],
        )

    # -- fetch --------------------------------------------------------------

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        self.status("Fetching NHL teams...")
        teams = self.api.get_nhl_teams()
        if not teams:
            raise ApiError("The provider returned no NHL teams")

        # Expansion teams have no 1994 slot; fetching them costs a round trip for
        # a roster `map_rosters` discards.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches an NHL94 Genesis ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            if self.provider == "nhl":
                players = self.api.get_hockey_squad(team.code, season)
                leaders = self.api.get_hockey_team_leaders(team.code, season)
            else:
                # ESPN's roster endpoint ignores the season, but it is still passed:
                # it is the cache key there, and a path segment for the leaders.
                players = self.api.get_hockey_squad(team.id, season)
                leaders = self.api.get_hockey_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    extra={"leaders": leaders or {}},
                )
            )

        if on_progress is not None:
            on_progress(1.0, "Complete")
        # No league endpoint for this game, so every field but `season` is synthesised.
        return LeagueData(
            league=League(
                id=0,
                name="NHL",
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
        ordered = self.mapper.select_roster(
            team_roster.players, leaders, max_players=MAX_PLAYERS_PER_SLOT
        )
        return self._append_unused(ordered, team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, list[NHL94GenPlayerRecord]] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(
                roster.players, leaders, max_players=MAX_PLAYERS_PER_SLOT
            )
            records = [
                self.mapper.map_player(player, roster.team.code, leaders.get(str(player.id), {}))
                for player in selected
            ]
            # `MODERN_NHL_TO_NHL94_GEN` maps 30 codes onto 26 slots — LAK/LA, NJD/NJ,
            # SJS/SJ and TBL/TB alias — so an empty roster arriving second must not
            # wipe the populated one that already took the slot.
            if not records and teams.get(slot):
                continue
            teams[slot] = records
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
        reader = NHL94GenesisRomReader(str(rom_path))
        if not reader.load() or not reader.validate():
            raise RomError(f"Not a valid NHL94 Genesis ROM: {rom_path}")

        self.status("Initializing ROM writer...")
        writer = NHL94GenesisRomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # Without this the game refuses to boot an edited cartridge.
        writer.disable_checksum()

        # An empty list would make `write_team_roster` zero-fill the region it was
        # going to patch. The range is re-checked because the reader guards only
        # `team_index >= TEAM_COUNT`: a negative key reads the four bytes before the
        # pointer table and writes wherever that word happens to point.
        targets = [slot for slot in rosters.filled_slots() if 0 <= slot < TEAM_COUNT]

        teams_patched = 0
        players_patched = 0
        for i, slot in enumerate(targets):
            if on_progress is not None:
                on_progress(i / len(targets), f"Writing {NHL94_GEN_TEAM_ORDER[slot]}...")
            players: list[NHL94GenPlayerRecord] = rosters.teams[slot]
            try:
                written = writer.write_team_roster(slot, players)
                if written <= 0:
                    # -1 is an error, 0 a region too small for one record. Either way
                    # nothing reached the image, so skip the header too — its lines
                    # table would index players that do not exist.
                    continue
                writer.write_team_header(slot, players, actual_count=written)
            except IndexError as exc:
                # A record chain running past the end of the image. Abort rather than
                # skip: the partial write is already in the writer's buffer.
                raise RomError(
                    f"Corrupt team block at slot {slot} in {rom_path}: "
                    f"the roster region runs past the end of the image"
                ) from exc
            teams_patched += 1
            players_patched += written

        self.status("Saving patched ROM...")
        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")
        writer.update_header_checksum()
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
