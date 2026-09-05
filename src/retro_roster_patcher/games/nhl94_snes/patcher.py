"""NHL 94 (SNES) on the unified Patcher interface.

The translation layer between the ported reader/writer/mapper -- a faithful copy
of an untested upstream, and kept that way -- and the contracts in
`core.patcher`. Where the ported code breaks one of those contracts it is worked
around here rather than fixed there.

Three things about this game are worth knowing before reading the code.

**Roster composition comes out of the ROM.** Byte 17 of a team block packs the
forward count in its high nibble and the defenceman count in its low nibble;
goalies are not encoded and are always 2. Upstream read those 28 triples inside
`patch_rom` and handed the same tuple to both the selection step and the header
write, so the two could not disagree. Here selection is `map_rosters`, which the
`Patcher` ABC deliberately gives no ROM: it must be runnable on a machine that
never sees the image, with a rosters file between the two halves. So the counts
travel in two hops -- `analyze_rom` publishes them in
`RomInfo.extra["roster_counts"]`, a caller hands them to
`map_rosters(roster_counts=...)`, and `map_rosters` records the ones it used on
each `NHL94TeamRecord` it emits. `patch` then reads the counts off the record
rather than off the ROM, which is what makes "selected as 2/14/7, header written
as 2/13/8" unrepresentable rather than merely unlikely. A caller that skips the
first hop -- `cli/commands.py` is one, since `map_rosters` is called through the
ABC's signature -- gets `(2, 14, 7)` for every slot, and gets a header that
describes that.

**The pointer table is at file offset 0xE25E7, in bank $9C.** That is 927 207
bytes in, so a file has to be at least ~950 KB before a single team pointer can
be read. NHL '94 (SNES) is an 8 Mbit LoROM -- 1 048 576 bytes -- so a real dump
is fine, but `NHL94SNESRomReader.validate` accepts any file of 649 728 bytes or
more, and tests nothing else about it. On anything between the two, every
pointer read returns None, every write is skipped and upstream reported success
having changed nothing; on anything above it, including a Genesis ROM and every
ISO, upstream said yes. `patch` checks the table is addressable
(`_pointer_table_fits`) and `analyze_rom` goes further and checks the 28 blocks
under it parse (`_looks_like_nhl94_snes`), because `analyze` probes every
registered patcher against one file.

**Nothing here touches a checksum, and that is correct.** The Genesis sibling
both NOPs out the cartridge's own verification and recomputes the header
checksum, and this game does neither. The SNES console does not verify the
header checksum word at $FFDC/$FFDE, and NHL '94 does not read it, so an
in-place patch of the same length boots unchanged; the community's own answer to
the question is that a stale checksum "doesn't create any problems". It is worth
fixing anyway for flash carts and verification tools, and it is not fixed here:
recomputing it is a change to the bytes this port promises are identical to
upstream's, so it belongs in its own commit with its own test.
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

    The condition is `_read_team_pointer`'s own -- it reads two bytes at
    `table + index * 4` and answers None when `ptr_off + 2` is past the end --
    evaluated for the last index rather than for each in turn.

    This is an IMPROVEMENT over upstream, which had no equivalent. `validate`
    bounds the size only from below, at a `ROM_SIZE_NO_HEADER` of 649 728 that
    is not this game's size in the first place, so files from there up to
    ~950 KB pass it and then read no pointer at all. Upstream's `patch_rom`
    treated that as 28 teams that happened not to fit and returned
    `success=True, teams_patched=0`. `analyze_rom` reports it as
    `is_valid=False` -- a file this patcher cannot patch is not this game -- and
    `patch` raises rather than writing an unmodified copy under a success
    return.
    """
    if reader.data is None:
        return False
    last = reader._ptr_table_offset() + (TEAM_COUNT - 1) * POINTER_SIZE
    return last + 2 <= len(reader.data)


#: Smallest team-block header this game's own writer can be pointed at. It puts
#: the player count at byte 17 and 8 lines of 7 slots at byte 19, so a header
#: shorter than this would have the line table overwriting the first player
#: record `_skip_team_header` says begins right after it.
MIN_TEAM_HEADER_SIZE = LINE_ASSIGN_OFFSET + LINE_COUNT * LINE_SLOTS

#: Largest one. Team blocks are addressed by a 16-bit offset within bank $9C,
#: which `snes_to_file_offset` folds into one 32 KB window, so a whole block --
#: header, records and strings -- lives inside 0x8000 bytes.
MAX_TEAM_BLOCK_SIZE = 0x8000

#: Bounds on a player record's length word, which counts itself and the name.
#: Both are the reader's own: `_read_team_city` treats anything under 3 as the
#: end-of-roster terminator, and `_read_length_prefixed_string` refuses anything
#: over 40.
MIN_RECORD_LENGTH = 3
MAX_RECORD_LENGTH = 40


def _looks_like_nhl94_snes(reader: NHL94SNESRomReader) -> bool:
    """Does this image hold 28 team blocks in the shape this game's code reads?

    IMPROVEMENT, with no upstream equivalent. `NHL94SNESRomReader.validate`
    tests the file's size and nothing else -- `>= ROM_SIZE_NO_HEADER`, with two
    exact sizes special-cased that the general test already covers -- so it says
    yes to every image of 634 KB or more in the user's library. That was
    survivable upstream, where the user named the game. It is not survivable
    here: `retro-roster analyze` probes every registered patcher against one
    ROM, and a size-only test makes this patcher claim NHL 94 for the Genesis,
    every other SNES cartridge, and every ISO.

    So each of the 28 pointers is dereferenced and the block under it is
    required to have this game's shape: a header long enough to hold the line
    table, small enough to sit in one bank, and a first player record whose
    length word is inside the bounds the reader itself enforces. Distinct
    blocks, too -- a file of constant garbage would otherwise point all 28 teams
    at one block that happened to parse.

    Every bound is taken from this package's own reader and writer rather than
    from a real image, and it is used for *detection only*: `analyze_rom` decides
    `is_valid` with it, and `patch` does not. That asymmetry is deliberate. A
    false positive costs the user every unrelated ROM they own being reported as
    NHL 94. A false negative -- if a genuine dump turns out to break one of these
    bounds, which nothing in this repository can check, because no real ROM may
    enter it -- costs only auto-detection, and `patch --game nhl94-snes` still
    runs.
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
        # `Any` for the same reason `nhl94_genesis` uses it: of the three methods
        # `fetch` calls, only `get_nhl_teams` has one signature on both clients.
        # `get_hockey_squad(team_id, season)` against
        # `get_hockey_squad(team_abbrev, season)` -- and `get_hockey_team_leaders`
        # the same way -- means no single type describes both, so calling the
        # wrong client's method is a runtime bug here and not a mypy error. Both
        # branches are pinned by tests. The client is built eagerly, and both
        # build their cache directory in their own constructor, so nothing here
        # reaches the network.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = NHL94SNESRomReader(str(rom_path))
        if not reader.load():
            # `load` catches its own OSError and answers False, so a missing
            # file, a revoked read bit and an EIO all arrive here as the same
            # False. That is the one case `analyze_rom` may raise for.
            raise RomError(f"Cannot read ROM: {rom_path}")
        info = reader.get_info()
        # `info.is_valid` is `validate()`, which is a size test alone. See
        # `_looks_like_nhl94_snes` for why that is not enough to answer the
        # question `analyze` asks, and why `patch` does not repeat this test.
        is_valid = info.is_valid and _looks_like_nhl94_snes(reader)

        extra: dict[str, Any] = {"has_header": info.has_header}
        if is_valid:
            # Lists and not tuples: `extra` is emitted as JSON and read back as
            # JSON, and a tuple comes back a list anyway. `map_rosters` accepts
            # exactly this value.
            #
            # Published only when the ROM is valid. On an invalid one every
            # triple would be the reader's `(2, 14, 7)` fallback, which reads as
            # 28 measurements and is 28 refusals to measure.
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

        # Only teams that exist as a slot in the 1994 ROM are worth fetching:
        # the expansion teams cost a network round trip and are then discarded
        # by `map_rosters`.
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
                # ESPN honours neither: the roster endpoint has no season and
                # `get_hockey_team_leaders` defaults to a hard-coded year.
                # Upstream passed the season to neither, and the two omissions
                # cost different things. The roster's season is the cache key,
                # so without it the first season ever fetched was served
                # forever. The leaders' season is a path segment of the request,
                # so the default meant a `--season 2024` run asked ESPN for a
                # different year's stats and stapled them to the squad.
                players = self.api.get_hockey_squad(team.id, season)
                leaders = self.api.get_hockey_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # Upstream left these on `self.team_stats`, an instance
                    # side channel between `fetch_rosters` and
                    # `map_rosters_to_nhl94` that no serialised rosters file
                    # could carry. In `extra` the whole result round-trips
                    # through JSON and the two steps can run in separate
                    # processes.
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

        `roster_counts` is `RomInfo.extra["roster_counts"]`, passed straight
        back in. It is a keyword-only extra on top of the ABC's signature --
        adding one is what let this stay ROM-independent without changing
        `Patcher.map_rosters` for the other games -- so a caller working through
        the ABC, `cli/commands.py` included, simply does not pass it and every
        slot is cut to `DEFAULT_ROSTER_COUNTS`. What matters is that whatever
        was used is recorded on the record, because `patch` writes the header
        from that and not from the ROM.
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
            # `MODERN_NHL_TO_NHL94` maps 30 codes onto 26 slots: LAK/LA, NJD/NJ,
            # SJS/SJ and TBL/TB each reach one slot, so two entries in
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
            # matched, and `patch` is what keeps the empty list away from
            # `write_team_roster`, which would zero-fill the region.
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
        """One validated (G, F, D) triple per slot.

        Validated rather than trusted because the value has crossed the JSON
        boundary since `analyze_rom` built it: it arrives as whatever the caller
        parsed, and a ragged row or a string would otherwise surface as a
        `ValueError` inside `select_roster` or as a wrong header byte.
        """
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
        # First, ahead of every other guard and ahead of the first status
        # message: it is the one check that costs no I/O, and the failure it
        # prevents is the writer choking on another game's record type with an
        # exception outside this library's hierarchy.
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
        # The image is read from disk twice -- once above, once by the writer's
        # own internal reader -- so one whole copy of the file is redundant I/O
        # per patch. Kept deliberately: it is what lets "not this game" fail
        # before any writer state exists, and the writer owns its reader for its
        # whole lifetime.
        writer = NHL94SNESRomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # `MappedRosters.filled_slots()` is unusable here and its docstring in
        # `core/models.py` says why: this game's mapped value is an object, so
        # every one of them is truthy however empty and it would return every
        # key. The emptiness that matters is the player list's -- an empty one
        # reaching `write_team_roster` zero-fills the whole region it was going
        # to patch, erasing that team's 1994 roster while this method still
        # reports success.
        #
        # The range is re-checked because those keys come from a plain dict that
        # may have crossed a JSON boundary since `map_rosters` built it. The
        # reader bounds-checks only `team_index >= TEAM_COUNT`, so a negative key
        # would read the bytes preceding the pointer table and treat whatever is
        # there as a team pointer.
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
                # `_get_team_player_region` walks the record chain with
                # `offset += length + STATS_SIZE` and its loop test only looks
                # at the offset it is about to read, so a chain that runs off
                # the end yields a region reaching past the image and the writes
                # inside it raise. Abort rather than skip the slot: the partial
                # write is already in the writer's buffer, so carrying on would
                # finalize a damaged ROM under a success return. Nothing has
                # reached disk at this point.
                raise RomError(
                    f"Corrupt team block at slot {slot} in {rom_path}: "
                    f"the roster region runs past the end of the image"
                ) from exc
            if written <= 0:
                # -1 is the writer's error return and 0 means the region it
                # found was too small for even one record. Either way nothing
                # reached the image, so nothing is counted and the header --
                # whose line table would index players that do not exist -- is
                # not written either.
                continue
            # The counts off the record and not off the ROM -- the line
            # assignments index forwards from 2 and defensemen from
            # `2 + num_forwards`, so a header written from a different triple
            # than the one that shaped this list labels real players with the
            # wrong role -- and then clamped to what actually reached the image.
            #
            # DELIBERATE DIVERGENCE, the second half of it: upstream wrote the
            # requested triple whatever happened downstream, so a roster the
            # writer truncated, or one a provider returned short, produced a
            # line table naming records that were never written. See
            # `rom_writer.header_counts`, which is where the arithmetic and its
            # two remaining edges are argued.
            written_forwards, written_defencemen = header_counts(
                written, team.num_forwards, team.num_defensemen
            )
            writer.write_team_header(slot, written_forwards, written_defencemen)
            teams_patched += 1
            # `written`, not `len(team.players)`. `write_team_roster` stops as
            # soon as the next record would not fit and drops the rest;
            # upstream added the requested count here and reported players that
            # never reached the image.
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
