"""NHL 94 (SNES) on the unified Patcher interface.

Roster composition comes out of the ROM: byte 17 of a team block packs the
forward count in its high nibble and the defenceman count in its low nibble, and
goalies are not encoded and are always 2. `map_rosters` is given no ROM, so the
counts travel in two hops -- `analyze_rom` publishes them in
`RomInfo.extra["roster_counts"]`, a caller passes them to
`map_rosters(roster_counts=...)`, and each `NHL94TeamRecord` records the triple
it was cut to. `patch` writes the header from the record, never from the ROM. A
caller that skips the first hop gets `(2, 14, 7)` everywhere.

The pointer table sits at file offset 0xE25E7, in bank $9C -- 927 207 bytes in,
so a file must be ~950 KB before one pointer can be read. `validate` accepts
anything from 649 728 bytes up and tests nothing else, so `patch` checks the
table is addressable (`_pointer_table_fits`) and `analyze_rom` checks the 28
blocks under it parse (`_looks_like_nhl94_snes`).

Nothing here touches a checksum: the SNES does not verify the header word at
$FFDC/$FFDE and NHL '94 does not read it, so an in-place patch of the same length
boots unchanged. The Genesis sibling needs both and does both.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...core.errors import ApiError, MappingError, RomError
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
from .models import (
    DEFAULT_ROSTER_COUNTS,
    NHL94_TEAM_ORDER,
    TEAM_COUNT,
    NHL94TeamRecord,
)
from .rom_reader import POINTER_SIZE, NHL94SNESRomReader
from .rom_writer import (
    LINE_ASSIGN_OFFSET,
    LINE_COUNT,
    LINE_SLOTS,
    NHL94SNESRomWriter,
    header_counts,
)
from .stat_mapper import NHL94StatMapper

#: One (goalies, forwards, defensemen) triple per ROM slot.
RosterCounts = list[tuple[int, int, int]]


def _pointer_table_fits(reader: NHL94SNESRomReader) -> bool:
    """Can every one of the 28 team pointers be read out of this file?

    `_read_team_pointer`'s own condition -- two bytes at `table + index * 4` --
    evaluated for the last index rather than for each in turn.
    """
    if reader.data is None:
        return False
    last = reader._ptr_table_offset() + (TEAM_COUNT - 1) * POINTER_SIZE
    return last + 2 <= len(reader.data)


#: Smallest team-block header this game's writer can be pointed at: the player
#: count sits at byte 17 and 8 lines of 7 slots at byte 19, so anything shorter
#: would have the line table overwrite the first player record.
MIN_TEAM_HEADER_SIZE = LINE_ASSIGN_OFFSET + LINE_COUNT * LINE_SLOTS

#: Largest one. Team blocks are addressed by a 16-bit offset within bank $9C,
#: folded into one 32 KB window, so a whole block lives inside 0x8000 bytes.
MAX_TEAM_BLOCK_SIZE = 0x8000

#: Bounds on a player record's length word, which counts itself and the name.
#: Under 3 is the end-of-roster terminator; over 40 the reader refuses.
MIN_RECORD_LENGTH = 3
MAX_RECORD_LENGTH = 40


def _looks_like_nhl94_snes(reader: NHL94SNESRomReader) -> bool:
    """Does this image hold 28 team blocks in the shape this game's code reads?

    Each of the 28 pointers is dereferenced and its block required to have a
    header long enough for the line table, small enough for one bank, and a first
    player record whose length word is inside the reader's own bounds. The blocks
    must also be distinct, or a file of constant garbage points all 28 teams at
    one block that happens to parse.

    Detection only: `analyze_rom` decides `is_valid` with it, `patch` does not, so
    a genuine dump that breaks a bound still patches under `--game nhl94-snes`.
    """
    data = reader.data
    if data is None or not _pointer_table_fits(reader):
        return False

    bases: set[int] = set()
    for index in range(TEAM_COUNT):
        base = reader._read_team_pointer(index)
        if base is None or base + 2 > len(data):
            return False
        header_size = data[base] | (data[base + 1] << 8)
        if not MIN_TEAM_HEADER_SIZE <= header_size < MAX_TEAM_BLOCK_SIZE:
            return False
        first_record = base + header_size
        if first_record + 2 > len(data):
            return False
        length = data[first_record] | (data[first_record + 1] << 8)
        if not MIN_RECORD_LENGTH <= length <= MAX_RECORD_LENGTH:
            return False
        bases.add(base)
    return len(bases) == TEAM_COUNT


@register(
    "nhl94-snes",
    platform="snes",
    sport="hockey",
    requires_slot_mapping=False,
    providers=("espn", "nhl"),
)
class NHL94SNESPatcher(Patcher):
    """Teams map to ROM slots by three-letter code, so no manual mapping step.

    Providers: `espn` for the current season, `nhl` for seasons back to 1993.
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
        self.mapper = NHL94StatMapper()
        # `Any`: the two clients take different arguments for squad and leaders, so
        # no single type describes both.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = NHL94SNESRomReader(str(rom_path))
        if not reader.load():
            raise RomError(f"Cannot read ROM: {rom_path}")
        info = reader.get_info()
        # `info.is_valid` is a size test alone; see `_looks_like_nhl94_snes`.
        is_valid = info.is_valid and _looks_like_nhl94_snes(reader)

        extra: dict[str, Any] = {"has_header": info.has_header}
        if is_valid:
            # Only for a valid image: on an invalid one every triple would be the
            # reader's `(2, 14, 7)` fallback, which reads as 28 measurements and is
            # 28 refusals to measure.
            extra["roster_counts"] = [
                list(reader.read_team_player_counts(i)) for i in range(TEAM_COUNT)
            ]

        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    current_name=slot.current_name,
                    display_name=slot.display_name,
                )
                for slot in info.team_slots
            ],
            extra=extra,
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
            raise ApiError("No fetched team matches an NHL94 SNES ROM slot")

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

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        roster_counts: Sequence[Sequence[int]] | None = None,
    ) -> MappedRosters:
        """Reduce league data to one `NHL94TeamRecord` per matched ROM slot.

        `roster_counts` is `RomInfo.extra["roster_counts"]` passed straight back
        in; omit it and every slot is cut to `DEFAULT_ROSTER_COUNTS`. Whatever was
        used is recorded on the record, because `patch` writes the header from
        that and not from the ROM.
        """
        self.check_slot_mapping(slot_mapping)
        counts = self._resolve_roster_counts(roster_counts)
        teams: dict[int, NHL94TeamRecord] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            num_goalies, num_forwards, num_defensemen = counts[slot]
            selected = self.mapper.select_roster(
                roster.players,
                leaders,
                num_goalies=num_goalies,
                num_forwards=num_forwards,
                num_defensemen=num_defensemen,
            )
            records = [
                self.mapper.map_player(player, roster.team.code, leaders.get(str(player.id), {}))
                for player in selected
            ]
            # `MODERN_NHL_TO_NHL94` maps 30 codes onto 26 slots -- LAK/LA, NJD/NJ,
            # SJS/SJ and TBL/TB alias -- so an empty roster arriving second must not
            # wipe the populated one that already took the slot.
            existing = teams.get(slot)
            if not records and existing is not None and existing.players:
                continue
            teams[slot] = NHL94TeamRecord(
                index=slot,
                name=NHL94_TEAM_ORDER[slot],
                city="",
                acronym="",
                players=records,
                num_goalies=num_goalies,
                num_forwards=num_forwards,
                num_defensemen=num_defensemen,
            )
        return MappedRosters(game_id=self.game_id, teams=teams)

    def _resolve_roster_counts(self, supplied: Sequence[Sequence[int]] | None) -> RosterCounts:
        """One validated (G, F, D) triple per slot. Validated because the value has
        crossed a JSON boundary since `analyze_rom` built it, and a ragged row would
        otherwise surface as a wrong header byte."""
        if supplied is None:
            return [DEFAULT_ROSTER_COUNTS] * TEAM_COUNT
        if len(supplied) != TEAM_COUNT:
            raise MappingError(
                f"roster_counts must hold {TEAM_COUNT} entries, one per ROM slot; "
                f"got {len(supplied)}"
            )
        counts: RosterCounts = []
        for index, row in enumerate(supplied):
            if len(row) != 3:
                raise MappingError(
                    f"roster_counts[{index}] must be (goalies, forwards, defensemen); got {row!r}"
                )
            goalies, forwards, defensemen = row
            if not all(
                isinstance(value, int) and value >= 0 for value in (goalies, forwards, defensemen)
            ):
                raise MappingError(
                    f"roster_counts[{index}] must hold three non-negative integers; got {row!r}"
                )
            counts.append((goalies, forwards, defensemen))
        return counts

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
        reader = NHL94SNESRomReader(str(rom_path))
        if not reader.load() or not reader.validate():
            raise RomError(f"Not a valid NHL94 SNES ROM: {rom_path}")
        if not _pointer_table_fits(reader):
            size = len(reader.data or b"")
            raise RomError(
                f"Not a valid NHL94 SNES ROM: {rom_path}: the team pointer table at "
                f"{reader._ptr_table_offset():#x} is past the end of a {size}-byte file"
            )

        self.status("Initializing ROM writer...")
        writer = NHL94SNESRomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # `filled_slots()` is unusable here: this game's mapped value is an object,
        # so every one is truthy however empty. An empty player list reaching
        # `write_team_roster` would zero-fill the region it was going to patch. The
        # range is re-checked because the reader guards only
        # `team_index >= TEAM_COUNT`: a negative key reads the bytes before the
        # pointer table and treats them as a team pointer.
        targets = sorted(
            slot for slot, team in rosters.teams.items() if 0 <= slot < TEAM_COUNT and team.players
        )

        teams_patched = 0
        players_patched = 0
        for index, slot in enumerate(targets):
            if on_progress is not None:
                on_progress(index / len(targets), f"Writing {NHL94_TEAM_ORDER[slot]}...")
            team: NHL94TeamRecord = rosters.teams[slot]
            try:
                written = writer.write_team_roster(slot, team.players)
            except IndexError as exc:
                # A record chain running past the end of the image. Abort rather than
                # skip: the partial write is already in the writer's buffer.
                raise RomError(
                    f"Corrupt team block at slot {slot} in {rom_path}: "
                    f"the roster region runs past the end of the image"
                ) from exc
            if written < 0:
                # -1: no region was found at all, so there is nothing for a header
                # to describe.
                continue
            # `written == 0` -- a region too small for one record -- still gets a
            # header. Upstream's behaviour, known wrong, preserved for byte fidelity:
            # the 49 bytes it writes are a line table naming records that do not
            # exist. Do not skip it.
            #
            # The counts come off the record, not the ROM -- the line assignments
            # index forwards from 2 and defencemen from `2 + num_forwards`, so a
            # header written from a different triple than the one that shaped this
            # list labels real players with the wrong role -- and `header_counts`
            # then clamps them to what actually reached the image.
            written_forwards, written_defencemen = header_counts(
                written, team.num_forwards, team.num_defensemen
            )
            writer.write_team_header(slot, written_forwards, written_defencemen)
            if written == 0:
                continue
            teams_patched += 1
            # `written`, not `len(team.players)`: the writer stops as soon as the
            # next record would not fit and drops the rest.
            players_patched += written

        self.status("Saving patched ROM...")
        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
