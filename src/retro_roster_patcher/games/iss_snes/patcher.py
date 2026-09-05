"""International Superstar Soccer (SNES) on the unified Patcher interface.

An explicit slot mapping is required and must stay required: the 27 slots are
national teams -- Germany, Italy, Holland -- and the data source is a club
league, so assigning league team *i* to ROM slot *i* is arbitrary.
`default_slot_mapping` offers that sequential assignment as a starting point
only. There is no `api_key`; do not add one back.

ESPN's squad is today's, its statistics are the season's: `get_squad` has no
season in its URL while `get_player_stats` honours the season in its path, so a
2019 patch gets 2025's names carrying 2019's numbers. The season still reaches
`get_squad` because it is part of the cache key.

`speed` and `stamina` come out of one formula -- see `stat_mapper.py`.

The writer is a ROM hack and `rom_writer.py`'s docstring is the map. Two
consequences reach this module: the order of the write calls in `patch` is load
bearing, and there is no ROM version check anywhere, so a revision other than
the one the ten 65816 patch addresses belong to is patched just as willingly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.errors import ApiError, CapabilityError, MappingError, RomError, as_rom_error
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
from ...sports.models import LeagueData, Team, TeamRoster
from .models import TEAM_ENUM_ORDER, TOTAL_TEAMS, ISSTeamRecord
from .rom_reader import ISSRomReader
from .rom_writer import MIN_PATCHABLE_SIZE, ISSRomWriter
from .stat_mapper import ISSStatMapper

#: The goalkeeper kit every patched team gets: green shirt, black shorts. A
#: constant, because no provider publishes a goalkeeper kit.
GK_KIT = ((0, 128, 0), (0, 0, 0))

#: The shorts colour in both outfield kits: `kit_home` is
#: `(primary, white, primary)` and `kit_away` is `(alternate, white, alternate)`.
KIT_SHORTS = (255, 255, 255)


def _parse_hex_colour(value: str) -> tuple[int, int, int] | None:
    """Read a `RRGGBB` or `#RRGGBB` provider colour, or `None` if it is neither."""
    text = value.lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


@register(
    "iss-snes",
    platform="snes",
    sport="soccer",
    requires_slot_mapping=True,
    providers=("espn",),
)
class ISSPatcher(Patcher):
    """27 national-team slots that no club competition maps onto by itself.

    `requires_slot_mapping=True`: there is no team code, abbreviation or name in
    the ROM to match a provider team against, so the caller says which club goes
    in which slot.

    The only provider is `espn`, which needs no credential. League ids are
    ESPN's: `--league-id 2001` is the Premier League.
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
        self.mapper = ISSStatMapper()
        self.api: Any = EspnClient(str(self.cache_dir), on_status, transport=transport)

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        reader = ISSRomReader(str(rom_path))
        with as_rom_error(rom_path):
            info = reader.get_rom_info()
        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=info.is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    current_name=(
                        f"First player: {slot.first_player}" if slot.first_player else ""
                    ),
                    display_name=slot.name,
                )
                for slot in info.team_slots
            ],
            # Which end of the file every offset is measured from, and not
            # reachable any other way once the reader is gone.
            extra={"has_header": info.has_header},
        )

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        if league_id is None:
            raise CapabilityError("iss-snes requires a league_id; there is no default league")

        if on_progress is not None:
            on_progress(0.05, "Fetching league info...")
        leagues = self.api.get_leagues(id=league_id, season=season)
        league = next(iter(leagues), None)
        if league is None:
            # Must stay inside this library's hierarchy: a bare `ValueError`
            # escapes a consumer catching `RetroRosterError`.
            raise ApiError(f"League {league_id} not found for season {season}")

        if on_progress is not None:
            on_progress(0.1, f"Fetching teams for {league.name}...")
        teams = self.api.get_teams(league_id, season)
        if not teams:
            raise ApiError(f"League {league_id} has no teams for season {season}")

        self.partial(
            LeagueData(
                league=league,
                teams=[TeamRoster(team=t, loading=True) for t in teams],
            )
        )

        rosters: list[TeamRoster] = []
        for i, team in enumerate(teams):
            if on_progress is not None:
                on_progress(0.1 + 0.8 * (i / len(teams)), f"Fetching squad: {team.name}...")
            # Built fresh, not mutated in place: the skeleton published above may
            # still be rendering in a caller.
            roster = TeamRoster(team=team)
            try:
                # `season` reaches the cache key and nothing else -- ESPN's squad
                # endpoint serves the squad as it stands today. Keep passing it,
                # or every season replays the first one ever fetched.
                roster.players = self.api.get_squad(team.id, season)
                try:
                    stats = self.api.get_player_stats(team.id, season)
                    roster.player_stats = {ps.player_id: ps for ps in stats}
                except Exception:
                    # Stats are optional: `map_player` falls back to position and
                    # age, so this costs ratings rather than the team.
                    self.status(
                        f"{team.name}: stats unavailable, ratings will use position defaults"
                    )
            except Exception as exc:
                # Deliberately broad, and `TransportLeak` is a `BaseException`
                # precisely so the network guard still escapes this.
                roster.error = f"Failed: {exc}"
                self.status(f"{team.name}: {roster.error}")
            rosters.append(roster)

        if on_progress is not None:
            on_progress(1.0, "Done!")
        return LeagueData(league=league, teams=rosters)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to one `ISSTeamRecord` per mapped ROM slot.

        Sparse: a key exists only for a slot the caller named. Nothing reads a
        missing one, and an unmapped slot keeps its 1994 squad.
        """
        self.check_slot_mapping(slot_mapping)
        entries = slot_mapping or []

        by_id = {roster.team.id: roster for roster in data.teams}
        teams: dict[int, ISSTeamRecord] = {}
        for entry in entries:
            if not 0 <= entry.slot_index < TOTAL_TEAMS:
                raise MappingError(
                    f"Slot {entry.slot_index} is outside the ISS range 0..{TOTAL_TEAMS - 1}"
                )
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    f"which is not in the fetched league data"
                )
            if entry.slot_index in teams:
                raise MappingError(
                    f"Slot {entry.slot_index} ({TEAM_ENUM_ORDER[entry.slot_index]}) "
                    f"is mapped more than once"
                )
            # The whole league, not just this team: percentiles are normalised
            # league-wide.
            record = self.mapper.map_team_with_league_context(roster, data.teams)
            self._apply_colours(record, roster.team)
            teams[entry.slot_index] = record
        return MappedRosters(game_id=self.game_id, teams=teams)

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
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")

        reader = ISSRomReader(str(rom_path))
        with as_rom_error(rom_path):
            # Called for the side effect: it sets `header_offset`, which every
            # offset the writer uses is measured from. Its 1 MB return value is a
            # heuristic and must not be enforced here -- only `data_fits` is
            # arithmetic, so only it may refuse a patch.
            reader.validate_rom()
            fits = reader.data_fits()
        if not fits:
            raise RomError(
                f"Too small to be an ISS ROM: {rom_path} holds "
                f"{Path(rom_path).stat().st_size} bytes and this patcher writes as far as "
                f"{MIN_PATCHABLE_SIZE} past any copier header"
            )

        # Re-checked: the keys may have crossed a JSON boundary since
        # `map_rosters` built them. Sorted, because `write_team_name_texts`
        # breaks a tie between two equally long names by whichever it met first.
        slots = sorted(slot for slot in rosters.teams if 0 <= slot < TOTAL_TEAMS)

        patched_names: dict[int, str] = {}
        patched_tile_names: dict[int, str] = {}
        patched_flag_colors: dict[int, tuple[tuple[int, int, int], tuple[int, int, int]]] = {}
        teams_patched = 0
        players_patched = 0

        with as_rom_error(rom_path):
            self.status("Preparing ROM...")
            # The constructor copies the input over the output and holds a
            # handle; `with` releases it when `write_name_tiles` raises.
            with ISSRomWriter(str(rom_path), str(output_path), reader.header_offset) as writer:
                for i, slot in enumerate(slots):
                    record: ISSTeamRecord = rosters.teams[slot]
                    if on_progress is not None:
                        on_progress(i / len(slots), f"Patching {record.name}...")

                    # Write order is load bearing: the three calls below the loop
                    # share a bank -- see `ISSRomWriter.write_name_tiles`.
                    writer.write_player_names(slot, record.players)
                    written = writer.write_player_data(slot, record.players)
                    writer.write_kit_colors(slot, record)
                    if record.flag_colors:
                        writer.write_predominant_color(slot, record.flag_colors[0])
                        patched_flag_colors[slot] = (record.flag_colors[0], record.flag_colors[1])

                    patched_names[slot] = record.name
                    patched_tile_names[slot] = record.short_name
                    # Unconditional: the name, the kit and the description land
                    # whether or not a squad was supplied.
                    teams_patched += 1
                    # `written`, not `len(record.players)`: the writer stops at
                    # 15 and a longer squad leaves the rest on the floor.
                    players_patched += written

                if on_progress is not None:
                    on_progress(0.80, "Writing flags...")
                writer.write_flag_tiles_and_colors(patched_flag_colors)

                if on_progress is not None:
                    on_progress(0.85, "Writing team names...")
                writer.write_team_name_texts(patched_names)
                writer.write_team_descriptions(patched_names)

                if on_progress is not None:
                    on_progress(0.90, "Writing in-game name tiles...")
                writer.write_name_tiles(patched_tile_names)

                if on_progress is not None:
                    on_progress(0.95, "Finalizing...")
                self.status("Saving patched ROM...")
                writer.finalize()

            if not Path(output_path).exists():
                raise RomError(f"Failed to write patched ROM to {output_path}")

        if on_progress is not None:
            on_progress(1.0, f"Done! Saved to {output_path}")
        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    def default_slot_mapping(self, data: LeagueData) -> list[SlotMapping]:
        """Sequential mapping: team 0 to slot 0, team 1 to slot 1, and so on.

        Teams beyond the 27th are dropped. This is a starting point a caller
        edits, never a substitute for an explicit mapping.
        """
        return [
            SlotMapping(
                slot_index=i,
                team_id=roster.team.id,
                team_name=roster.team.name,
            )
            for i, roster in enumerate(data.teams)
            if i < TOTAL_TEAMS
        ]

    @staticmethod
    def _apply_colours(record: ISSTeamRecord, team: Team) -> None:
        """Copy the provider's team colours onto the ROM record.

        They must live on the record, not beside it: `MappedRosters` is what
        crosses from `map_rosters` to `patch`, and a `Team` does not travel with
        it. Four things read them: both outfield kits, the flag palette, the flag
        tiles and the predominant-colour byte.
        """
        primary = _parse_hex_colour(team.color)
        alternate = _parse_hex_colour(team.alternate_color)
        if primary is not None:
            record.kit_home = (primary, KIT_SHORTS, primary)
        if alternate is not None:
            record.kit_away = (alternate, KIT_SHORTS, alternate)
        record.kit_gk = GK_KIT
        if primary is not None:
            record.flag_colors = [primary, alternate if alternate is not None else primary]
