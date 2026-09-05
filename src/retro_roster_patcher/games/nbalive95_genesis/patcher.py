"""NBA Live 95 (Genesis) on the unified Patcher interface.

Every record address is a hardcoded literal: `TEAM_ROSTER_ADDRESSES` holds 30
absolute file offsets transcribed from Team-95's `ConstantsTeam.h`, deliberately
not evenly spaced -- team 17's table is at 0x00044AF4 and team 18's at 0x001F4EF4,
1.75 MB further on. Nothing derives them, so a differently-versioned dump does not
fail; it writes into whatever those offsets happen to address, which is what
`_looks_like_nbalive95` exists to catch.

Two checksum mechanisms and both are used: `apply_patches` replaces six bytes of
68000 code at 0x690 with three NOPs so the cartridge stops verifying itself, and
`_fix_checksum` recomputes the Genesis header checksum at 0x18E. The second runs
inside `NBALive95RomWriter.finalize`, so `patch` must not call it as well.

A player record is 69 fixed bytes plus a name and records are packed with no
padding, so a name's budget is the gap to the next pointer. `write`'s `load`
measures all 360 gaps up front; the last record of each team, having no next
pointer, is measured by scanning for the two nulls that end its name.

`RomSlot.current_name` carries a player and says so: this reader never parses the
team-name strings, so `analyze_rom` writes `"First player: Stacey Augmon"` and
puts the constant team name in `display_name`.
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
    BYTE_TO_POSITION,
    NAME_LENGTH,
    NBA_TEAM_COUNT,
    NBALIVE95_TEAM_ORDER,
    OFF_NAME,
    OFF_POSITION,
    PLAYERS_PER_TEAM,
    TEAM_COUNT,
    TEAM_POINTER_SIZE,
    TEAM_ROSTER_ADDRESSES,
    NBALive95TeamRecord,
)
from .rom_reader import NBALive95RomReader
from .rom_writer import NBALive95RomWriter
from .stat_mapper import NBALive95StatMapper

#: File offset one past the last byte any team's pointer table occupies. Derived
#: with `max` because the addresses are transcribed literals and need not be sorted.
_LAST_POINTER_END = max(TEAM_ROSTER_ADDRESSES) + PLAYERS_PER_TEAM * TEAM_POINTER_SIZE

#: The fewest printable ASCII bytes a 24-byte name field must hold.
MIN_NAME_ASCII = 3


def _pointer_tables_fit(reader: NBALive95RomReader) -> bool:
    """Can all 360 team pointers be read out of this file?

    `_get_player_offset`'s own condition -- four bytes at `roster_off + slot * 4`
    -- evaluated for the furthest of the 360 rather than for each in turn.
    """
    if reader.data is None:
        return False
    return _LAST_POINTER_END <= len(reader.data)


def _looks_like_nbalive95(reader: NBALive95RomReader) -> bool:
    """Does this image hold 360 player records in the shape this game's code reads?

    All 30 pointer tables are dereferenced and each record they address must have

    - a non-zero pointer with the whole 93-byte record inside the file;
    - byte 1 a position this game defines, `BYTE_TO_POSITION`'s own domain;
    - at least three printable ASCII bytes in the 24-byte name field;
    - a pointer distinct from the other 359, or a file of constant bytes aims
      every slot at one record that happens to parse.

    The 16 rating bytes are deliberately not bounded to the writer's 0-99 clamp;
    the pointer test already does nearly all the discriminating.

    Detection only: `analyze_rom` decides `is_valid` with it, `patch` does not, so
    a genuine dump that breaks a bound still patches under `--game`.
    """
    data = reader.data
    if data is None or not _pointer_tables_fit(reader):
        return False

    seen: set[int] = set()
    for team_index in range(TEAM_COUNT):
        for slot in range(PLAYERS_PER_TEAM):
            offset = reader._get_player_offset(team_index, slot)
            if offset == 0:
                return False
            if data[offset + OFF_POSITION] not in BYTE_TO_POSITION:
                return False
            name = data[offset + OFF_NAME : offset + OFF_NAME + NAME_LENGTH]
            if sum(1 for b in name if 0x20 <= b <= 0x7E) < MIN_NAME_ASCII:
                return False
            seen.add(offset)
    return len(seen) == TEAM_COUNT * PLAYERS_PER_TEAM


@register(
    "nbalive95-genesis",
    platform="genesis",
    sport="basketball",
    requires_slot_mapping=False,
    providers=("espn",),
)
class NBALive95Patcher(Patcher):
    """Teams map to ROM slots by abbreviation, so no manual mapping step.

    30 slots exist and 27 are patched: slots 27-29 are the East All-Stars, the
    West All-Stars and the Slammers, which no real NBA team maps to.
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
        self.mapper = NBALive95StatMapper()
        self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = NBALive95RomReader(str(rom_path))
        if not reader.load():
            raise RomError(f"Cannot read ROM: {rom_path}")
        info = reader.get_info()
        # `info.is_valid` looks at team 0 alone; see `_looks_like_nbalive95`.
        is_valid = info.is_valid and _looks_like_nbalive95(reader)

        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    # labelled, because it is a player and not a team
                    current_name=(
                        f"First player: {slot.first_player}" if slot.first_player else ""
                    ),
                    display_name=slot.name,
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
        self.status("Fetching NBA teams...")
        teams = self.api.get_nba_teams()
        if not teams:
            raise ApiError("The provider returned no NBA teams")

        # Toronto, Memphis and New Orleans have no 1994 slot; fetching them costs
        # two round trips each for a roster `map_rosters` discards.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches an NBA Live 95 ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            # The squad endpoint has no season in its URL but does have one in its
            # cache key; for the leaders it is a path segment.
            players = self.api.get_basketball_squad(team.id, season)
            leaders = self.api.get_basketball_team_leaders(team.id, season)
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
                name="NBA",
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
        ordered = self.mapper.select_roster(team_roster.players, leaders)
        return self._append_unused(ordered, team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to one `NBALive95TeamRecord` per matched ROM slot.

        Sparse: a key exists only for a slot some fetched team mapped to.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, NBALive95TeamRecord] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            # `NBA_TEAM_COUNT` and not `TEAM_COUNT`: slots 27-29 are the two All-Star
            # teams and the Slammers.
            if slot is None or not 0 <= slot < NBA_TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders)
            records = [
                self.mapper.map_player(player, leaders.get(str(player.id), {}))
                for player in selected
            ]
            # `MODERN_NBA_TO_NBALIVE95` maps 34 codes onto 27 slots -- GS/GSW,
            # BKN/NJN, NYK/NY, SA/SAS, OKC/SEA, UTA/UTAH and WAS/WSH alias -- so an
            # empty roster arriving second must not wipe the populated one that
            # already took the slot.
            existing = teams.get(slot)
            if not records and existing is not None and existing.players:
                continue
            teams[slot] = NBALive95TeamRecord(
                index=slot,
                name=NBALIVE95_TEAM_ORDER[slot],
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
        reader = NBALive95RomReader(str(rom_path))
        if not reader.load() or not reader.validate():
            raise RomError(f"Not a valid NBA Live 95 ROM: {rom_path}")
        if not _pointer_tables_fit(reader):
            size = len(reader.data or b"")
            raise RomError(
                f"Not a valid NBA Live 95 ROM: {rom_path}: the team pointer tables end at "
                f"{_LAST_POINTER_END:#x}, past the end of a {size}-byte file"
            )

        self.status("Initializing ROM writer...")
        writer = NBALive95RomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # Six bytes of 68000 code at 0x690 become three NOPs so the cartridge stops
        # verifying itself. A ROM whose records changed and whose self-check did not
        # simply refuses to boot.
        writer.apply_patches()

        # `filled_slots()` is unusable here: this game's mapped value is an object,
        # so every one is truthy however empty. The range is re-checked because the
        # writer guards only `team_index >= TEAM_COUNT`: a negative key resolves to
        # offset 0 and reads the Genesis interrupt vectors as player pointers.
        targets = sorted(
            slot
            for slot, team in rosters.teams.items()
            if 0 <= slot < NBA_TEAM_COUNT and team.players
        )

        teams_patched = 0
        players_patched = 0
        for index, slot in enumerate(targets):
            if on_progress is not None:
                on_progress(index / len(targets), f"Writing {NBALIVE95_TEAM_ORDER[slot]}...")
            team: NBALive95TeamRecord = rosters.teams[slot]
            written = writer.write_team_roster(slot, team.players)
            if written <= 0:
                # -1 is an error, 0 means not one of the 12 pointers resolved.
                continue
            teams_patched += 1
            # `written`, not `len(team.players)`: a slot whose pointer does not
            # resolve is skipped and the loop carries on.
            players_patched += written

        self.status("Saving patched ROM...")
        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")
        # `finalize` recomputes the header checksum at 0x18E itself, so nothing here
        # may call `_fix_checksum` as well.
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
