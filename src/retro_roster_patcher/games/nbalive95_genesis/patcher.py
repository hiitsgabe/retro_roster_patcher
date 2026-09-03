"""NBA Live 95 (Genesis) on the unified Patcher interface.

The translation layer between the ported reader/writer/mapper -- a faithful copy
of an untested upstream, and kept that way -- and the contracts in
`core.patcher`. Where the ported code breaks one of those contracts it is worked
around here rather than fixed there.

Four things about this game are worth knowing before reading the code.

**Every record address is a hardcoded literal.** `TEAM_ROSTER_ADDRESSES` holds
30 absolute file offsets transcribed from Team-95's `ConstantsTeam.h`, and they
are deliberately not evenly spaced: team 17's table is at 0x00044AF4 and team
18's at 0x001F4EF4, 1.75 MB further on. Nothing in this package derives them,
so a differently-versioned dump of the same game does not fail -- it writes
player records into whatever those offsets happen to address. That is precisely
the class of accident `_looks_like_nbalive95` exists to catch, and it is why
that check dereferences all 360 pointers rather than the one team 0 pointer the
ported `validate` looks at.

**Two checksum mechanisms, and both are used.** `apply_patches` replaces six
bytes of 68000 code at 0x690 with three NOPs so the cartridge stops verifying
itself, and `_fix_checksum` recomputes the Genesis header checksum at 0x18E as a
16-bit sum of big-endian words from 0x200 to the end of the file. The second one
runs *inside* `NBALive95RomWriter.finalize`, which is where upstream put it and
where it stays. The NHL 94 Genesis sibling splits the two the other way and has
its patcher call `update_header_checksum` explicitly; this one does not, so
`patch` below calls neither and must not, because calling `_fix_checksum` here
would sum the image twice and write the same word twice for no gain.

**A player record is 69 fixed bytes plus a name, and the names are packed.**
There is no padding between records, so how long a name may be is not a property
of the record but of the gap to the next pointer. `NBALive95RomWriter.load`
measures all 360 gaps up front -- O(teams x players), eagerly, once -- and the
last record of each team, which has no next pointer, is measured by scanning
forward for the two null bytes that end its name.

**`RomSlot.current_name` carries a player, and says so.** The upstream slot
record is `(index, name, first_player)`: `name` is `NBALIVE95_TEAM_ORDER[i]`, a
constant, and `first_player` is the only thing in it actually read from the
image. This reader never parses the team-name strings at all -- `models.py` has
the metadata offsets and nothing dereferences them -- so there is no team name
to put in `current_name`. Rather than drop the one ROM-derived signal the slot
has, or file a player under a field a UI will render as a team, `analyze_rom`
writes `"First player: Stacey Augmon"`, and an empty string where no record
could be read. `display_name` takes the constant, which is what it is for and is
distinct across all 30 slots.
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
#: from the table itself rather than from `TEAM_ROSTER_ADDRESSES[-1]`, because
#: the addresses are transcribed literals and nothing in this package requires
#: them to be sorted.
_LAST_POINTER_END = max(TEAM_ROSTER_ADDRESSES) + PLAYERS_PER_TEAM * TEAM_POINTER_SIZE

#: The fewest printable ASCII bytes a 24-byte name field must hold. Taken from
#: `NBALive95RomReader.validate`, which applies exactly this test to one record.
MIN_NAME_ASCII = 3


def _pointer_tables_fit(reader: NBALive95RomReader) -> bool:
    """Can all 360 team pointers be read out of this file?

    The condition is `_get_player_offset`'s own -- it reads four bytes at
    `roster_off + slot * 4` and answers 0 when `ptr_off + 4` is past the end --
    evaluated for the furthest of the 360 rather than for each in turn.

    This is an IMPROVEMENT over upstream, which had no equivalent. `validate`
    bounds the size from below at `ROM_SIZE_MIN`, 1 572 864 bytes, and then
    dereferences team 0 alone at 0x3FEB4. Team 29's table ends 491 740 bytes
    past that minimum, so every file between 1.5 MB and ~2.0 MB passed
    `validate`, read no pointer at all for teams 18-29, silently skipped every
    write for them and still reported success. `analyze_rom` reports such a file
    as `is_valid=False` -- a file this patcher cannot fully patch is not this
    game -- and `patch` raises rather than writing a partly-unmodified copy
    under a success return.
    """
    if reader.data is None:
        return False
    return _LAST_POINTER_END <= len(reader.data)


def _looks_like_nbalive95(reader: NBALive95RomReader) -> bool:
    """Does this image hold 360 player records in the shape this game's code reads?

    IMPROVEMENT, with no upstream equivalent worth the name.
    `NBALive95RomReader.validate` tests four things: a size band, a title
    substring, that team 0's first pointer is in range, and that team 0 slot 0's
    name field holds three printable bytes. The title test is the weakest link
    -- it is `if "NBA" in title and "95" not in title: return False`, so it
    rejects NBA Live 96 and passes unconditionally on any header that does not
    mention the NBA at all, which is every non-EA Genesis cartridge. That was
    survivable upstream, where the user named the game. It is not survivable
    here: `retro-roster analyze` probes every registered patcher against one
    ROM, and a check that only ever looks at team 0 makes this patcher claim
    most of a Genesis library.

    So all 30 pointer tables are dereferenced and each of the 360 records they
    address is required to have this game's shape:

    - the pointer is non-zero and the whole 93-byte record is inside the file,
      which is `_get_player_offset`'s own bound and the one that decides whether
      the writer touches the slot at all;
    - byte 1 is a position this game defines, which is `BYTE_TO_POSITION`'s own
      domain -- the reader renders anything else as the string `"?7"`;
    - the 24-byte name field holds at least three printable ASCII bytes, which
      is `validate`'s own test, applied to all 360 records instead of one;
    - and all 360 pointers are distinct, because a file of constant bytes would
      otherwise aim every slot at one record that happened to parse.

    The 16 rating bytes are deliberately NOT bounded to the writer's 0-99 clamp.
    It is the one derived bound whose upper end is a guess about a real image
    rather than about this package's code, and the pointer test above already
    does nearly all the discriminating: a 32-bit big-endian word landing inside
    a 2 MB file is a 1-in-2048 accident, and this asks for 360 of them at fixed
    offsets, all distinct.

    Every bound is taken from this package's own reader rather than from a real
    image, and it is used for *detection only*: `analyze_rom` decides `is_valid`
    with it, and `patch` does not. That asymmetry is deliberate. A false
    positive costs the user every unrelated ROM they own being reported as NBA
    Live 95. A false negative -- if a genuine dump turns out to break one of
    these bounds, which nothing in this repository can check, because no real
    ROM may enter it -- costs only auto-detection, and
    `patch --game nbalive95-genesis` still runs.
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

    30 slots exist and 27 are patched. Slots 27-29 are the East All-Stars, the
    West All-Stars and the Slammers, which no real NBA team maps to; upstream
    built a record for all 30 and dropped anything landing at 27 or above, and
    `map_rosters` below returns keys for the 27 only.

    ESPN is the only provider. `season` reaches `get_basketball_squad` as a
    cache key and `get_basketball_team_leaders` as a path segment of the
    request.
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
        # Built eagerly, and `EspnClient.__init__` creates its cache directory,
        # so constructing this patcher can raise `StorageError`. Nothing here
        # reaches the network.
        self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = NBALive95RomReader(str(rom_path))
        if not reader.load():
            # `load` catches its own OSError and answers False, so a missing
            # file, a revoked read bit and an EIO all arrive here as the same
            # False. That is the one case `analyze_rom` may raise for.
            #
            # DELIBERATE DIVERGENCE. Upstream returned
            # `NBALive95RomInfo(path=rom_path, size=0)` here -- a size of 0 for a
            # file that may be 2 MB, and `is_valid=False`, which is the same
            # answer it gives for a readable image of a different game. The
            # library needs those two apart: `cmd_analyze` catches `RomError`
            # per patcher and continues, and treats `is_valid=False` as a
            # considered "not this game".
            raise RomError(f"Cannot read ROM: {rom_path}")
        info = reader.get_info()
        # `info.is_valid` is `validate()`, which looks at team 0 alone. See
        # `_looks_like_nbalive95` for why that is not enough to answer the
        # question `analyze` asks, and why `patch` does not repeat this test.
        is_valid = info.is_valid and _looks_like_nbalive95(reader)

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

        # Only teams that exist as a slot in the 1994 ROM are worth fetching:
        # Toronto, Memphis and New Orleans cost two network round trips each and
        # are then discarded by `map_rosters`.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches an NBA Live 95 ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            # DELIBERATE DIVERGENCE: upstream called
            # `get_basketball_squad(team.id)` with no season at all, and the two
            # omissions cost different things. The squad endpoint has no season
            # in its URL but does have one in its cache key, so without it the
            # first season ever fetched was served forever. The leaders endpoint
            # takes the season as a path segment, and upstream did pass it
            # there.
            players = self.api.get_basketball_squad(team.id, season)
            leaders = self.api.get_basketball_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # Upstream left these on `self.team_stats`, an instance side
                    # channel between `fetch_rosters` and `map_rosters` that no
                    # serialised rosters file could carry -- and that
                    # `map_rosters` reached for with `getattr(self,
                    # "team_stats", {})`, so calling the two out of order was a
                    # silent downgrade to position defaults rather than an
                    # error. In `extra` the whole result round-trips through
                    # JSON and the two steps can run in separate processes.
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

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to one `NBALive95TeamRecord` per matched ROM slot.

        Sparse: a key exists only for a slot some fetched team mapped to, where
        upstream always built all 30 records and left 27-29 empty. Nothing reads
        an empty record, and `patch` skips it either way.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, NBALive95TeamRecord] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            # `>= NBA_TEAM_COUNT` and not `>= TEAM_COUNT`: slots 27-29 are the
            # two All-Star teams and the Slammers, and upstream skipped them
            # here for the same reason. No key in `MODERN_NBA_TO_NBALIVE95`
            # reaches them today, so this is a guard and not a filter.
            if slot is None or not 0 <= slot < NBA_TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders)
            records = [
                self.mapper.map_player(player, leaders.get(str(player.id), {}))
                for player in selected
            ]
            # `MODERN_NBA_TO_NBALIVE95` maps 34 codes onto 27 slots: GS/GSW,
            # BKN/NJN, NYK/NY, SA/SAS, OKC/SEA, UTA/UTAH and WAS/WSH each name
            # one slot twice, so two entries in `data.teams` can target the same
            # one. Upstream kept its rosters in a dict keyed by team code and
            # only stored a team whose squad was non-empty, so an empty alias
            # could never displace a populated one; here the slot is assigned
            # directly. Without this guard an empty alias arriving second would
            # wipe the populated record, `patch` would skip the slot, and the run
            # would report success with `teams_patched` short by one and the
            # 1994 roster still in place.
            #
            # An empty roster that collides with nothing still takes the slot:
            # the mapped result keeps showing which slots a provider team
            # matched, and `patch` is what keeps the empty list away from the
            # writer.
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
        # First, ahead of every other guard and ahead of the first status
        # message: it is the one check that costs no I/O, and the failure it
        # prevents is the writer choking on another game's record type with an
        # exception outside this library's hierarchy.
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
        # The image is read from disk twice -- once above, once by the writer's
        # own internal reader -- so one whole copy of the file is redundant I/O
        # per patch. Kept deliberately: it is what lets "not this game" fail
        # before any writer state exists, and the writer owns its reader for its
        # whole lifetime.
        writer = NBALive95RomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # Six bytes of 68000 code at 0x690 become three NOPs so the cartridge
        # stops verifying itself. Unconditional, and before any record is
        # written, exactly as upstream had it: a ROM whose records changed and
        # whose self-check did not simply refuses to boot.
        writer.apply_patches()

        # `MappedRosters.filled_slots()` is unusable here and its docstring in
        # `core/models.py` says why: this game's mapped value is an object, so
        # every one of them is truthy however empty and it would return every
        # key. The emptiness that matters is the player list's.
        #
        # The range is re-checked because those keys come from a plain dict that
        # may have crossed a JSON boundary since `map_rosters` built it.
        # `write_team_roster` guards only `team_index >= TEAM_COUNT`, so a
        # negative key would reach `_get_team_roster_offset`, get 0 back, and
        # read the Genesis interrupt vectors at the head of the file as if they
        # were player pointers.
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
                # -1 is the writer's error return and 0 means not one of the 12
                # pointers resolved. Either way nothing reached the image, so
                # nothing is counted.
                continue
            teams_patched += 1
            # `written`, not `len(team.players)`: `write_player` answers False
            # for a slot whose pointer does not resolve and the loop carries on,
            # so the two numbers differ on a partly-addressable team.
            # `core/models.py` defines `players_patched` as records that reached
            # the image.
            players_patched += written

        self.status("Saving patched ROM...")
        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")
        # `finalize` recomputes the Genesis header checksum at 0x18E itself,
        # before it writes. Nothing here may call `_fix_checksum` as well; see
        # this module's docstring.
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
