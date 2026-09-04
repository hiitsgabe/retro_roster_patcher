"""NHL 07 (PSP) on the unified Patcher interface.

The translation layer between the ported reader/writer/mapper and the contracts
in `core.patcher`. Five things about this game are unlike every other patcher in
this library and are worth reading before the code.

**It patches named database records, not byte offsets.** There is not one
hardcoded player address in the package. Every write is
`table.write_record(idx, {"FNME": ..., "SACC": ...})` against four-character
field names whose widths and bit offsets come out of the file's own headers. So
the failure modes are different: a mistyped field name is silently ignored by
`TDBTable.write_record`, and a wrong record index writes a real player over a
different real player. Neither shows up as a crash.

**Records are reached through a four-hop chain, and only the last hop is a
position.** For a team slot `t`:

    ROST.find_records("TEAM", t)  ->  a list of ROST record positions
    ROST[i]["INDX"]               ->  a PLAY record's INDX value
    PLAY[...]["ID__"]             ->  a player id
    SPBT / SPAI / SGAI            ->  the record whose INDX is that player id

Nothing in that chain is the identity function. `patch` builds three
`INDX -> position` maps to walk it, and a slot is classified as a goalie slot by
whether its player id has an SGAI entry -- not by what the disc's bio says the
position is -- because the attributes have to go to a table that has a row for
him.

**The writes are mirrored across three TDB files.** `nhl2007.tdb` is the master
and holds every table; `nhlbioatt.tdb` holds a second copy of SPBT/SPAI/SGAI and
`nhlrost.tdb` a second copy of ROST. Every record written to the master is
written to its mirror as well, at the same index, when the mirror has one.

**`analyze_rom` and `patch` do not apply the same checks**, deliberately, and
`_db_viv_extent_fits` versus `NHL07PSPRomReader.validate` is the split. See
`analyze_rom`.

**A compressed disc image is refused, with a message saying so.** See
`_COMPRESSED_IMAGE_MAGIC`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
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
from ...formats.ea_tdb import TDBFile, TDBTable, bigf_parse
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.models import League, LeagueData, TeamRoster
from ...sports.nhl import NhlApiClient
from .models import (
    NHL07_TEAM_NAMES,
    SLOT_COUNT,
    TDB_BIOATT,
    TDB_MASTER,
    TDB_ROSTER,
    NHL07PlayerRecord,
)
from .rom_reader import ISO_SECTOR_SIZE, NHL07PSPRomReader
from .rom_writer import PROGRESS_COPY_END, PROGRESS_RECORDS_END, NHL07PSPRomWriter
from .stat_mapper import MAX_PLAYERS, NHL07StatMapper

# Magic bytes of the compressed PSP disc formats, and the name to put in the
# error. All four are containers around an ISO 9660 image: nothing in this
# package can read one, and `analyze_rom` says so rather than reporting the
# user's perfectly good backup as "not NHL 07".
#
# Why this patcher does not decompress them instead:
#
#   * `.zso` is LZ4 and `.jso`/`.dax` are LZO-family. Neither codec is in the
#     standard library, and this library has zero runtime dependencies.
#     Supporting `.cso` alone would leave three of the four still lying.
#   * Even for `.cso`, which is zlib and therefore reachable, *reading* is only
#     half the job. `patch` seeks to `db_lba * 2048` in the output and
#     overwrites the archive in place, which a CSO cannot do: its blocks are
#     individually compressed and variable-length, so any edit rewrites the
#     block index and every offset after it. That is a CSO *writer*, a format
#     this repository has no reference for and no real image to check against.
#   * Refusing is reversible and cheap for the user -- `maxcso --decompress`
#     turns a CSO back into the ISO this patcher does read. A half-implemented
#     writer that produced an image the PSP would not boot is neither.
#
# The upstream pygame front end advertised `.cso` in its ROM-finder
# configuration while the reader had no CSO support at all, so a user who
# picked one got "invalid ISO". Advertising a capability that does not exist is
# the same defect class as the size constants Phases 1 and 2 found; this is the
# honest half of that fix, and the front end's file-extension list is the other
# half and does not live in this library.
_COMPRESSED_IMAGE_MAGIC = {
    b"CISO": "CSO",
    b"ZISO": "ZSO",
    b"JISO": "JSO",
    b"DAX\x00": "DAX",
}

# How many bytes of the file `_compressed_image_format` needs to see.
_MAGIC_LENGTH = 4


def _compressed_image_format(rom_path: Path) -> str | None:
    """The name of the compressed-image format this file is, or None.

    Reads four bytes. A file too short to hold a magic number is not one of
    these formats, and is refused later by `NHL07PSPRomReader.load`'s size floor
    for a reason that names the actual problem.

    Raises:
        OSError: the file is missing or unreadable. Both callers are inside an
            `as_rom_error` block, which is what turns that into `RomError`.
    """
    with open(rom_path, "rb") as f:
        head = f.read(_MAGIC_LENGTH)
    return _COMPRESSED_IMAGE_MAGIC.get(head)


def _db_viv_extent(reader: NHL07PSPRomReader) -> tuple[int, int]:
    """(first byte, last byte + 1) of `db.viv` as the ISO's directory declares it.

    (0, 0) when the archive cannot be located at all, which the callers treat
    the same way as an extent that does not fit.
    """
    db_lba, db_size, _ = reader.find_db_viv_location()
    if db_lba == 0:
        return 0, 0
    start = db_lba * ISO_SECTOR_SIZE
    return start, start + db_size


def _db_viv_extent_fits(rom_path: Path, reader: NHL07PSPRomReader) -> bool:
    """Does the whole of `db.viv` lie inside the file?

    This is the **arithmetic bound**, and unlike
    `NHL07PSPRomReader.validate` it guards `analyze_rom` *and* `patch`. The
    difference is what kind of claim each makes. `validate` guesses at meaning
    -- "an archive holding a file called `nhlbioatt.tdb` that decompresses to a
    TDB is probably NHL 07" -- and a wrong guess costs a user auto-detection,
    which `patch --game nhl07-psp` routes around. This is arithmetic on numbers
    the file states about itself, and a file that fails it provably cannot be
    patched, so exempting `patch` would preserve exactly the failure the check
    exists to kill.

    The arithmetic, explicitly. The ISO 9660 directory record for `DB.VIV`
    declares an extent LBA and a length in bytes. Mode 1 sectors are 2048 bytes
    with no header, so the archive occupies

        [ lba * 2048 , lba * 2048 + size )

    and the file must be at least `lba * 2048 + size` bytes long. A 5 000-sector
    LBA with a 40 000-byte archive needs 10 240 000 + 40 000 = 10 280 000 bytes.

    What goes wrong without it is a silent-corruption path that runs the entire
    length of the stack, and every layer of it is *documented* to stay silent:

        `_extract_db_viv` does `f.read(size)` and gets fewer bytes, silently;
        `bigf_parse` trusts the file count and manufactures entries out of what
        it can reach -- `formats/ea_tdb.py`, inherited defect 1;
        `bigf_extract` slices past the end and returns a short RefPack stream;
        `refpack_decompress` returns short for a truncated stream and never
        pads -- inherited contract 3;
        `TDBFile._parse_table` takes a short `_raw_data` without complaint;
        `TDBFile.serialize` then **shrinks its own output**, moving every later
        table's offset -- inherited contract 2.

    The result is an archive that recompresses smaller, fits every size check
    below it, is written to the disc, and boots to a corrupted database. And
    `PatchResult` would report it as a success with a full count of teams
    patched. Refusing at the top is the only place in that chain where the fact
    is still visible.

    None of this can be checked against a real disc; no ISO may enter this
    repository. The numbers come from the image's own directory record.
    """
    start, end = _db_viv_extent(reader)
    if end == 0:
        return False
    return end <= os.path.getsize(rom_path)


def _live_records(table: TDBTable) -> range:
    """The record positions of `table` that are both live and allocated.

    **This is the bound `formats/ea_tdb.py` hands to its consumers.** That
    module deliberately never checks `currentRecords` against `maxRecords`:
    clamping would make `serialize` write back a count it never read and break
    the round-trip property the format layer is tested on, and raising would
    refuse a whole disc over one header word. So the fact travels and the policy
    is the consumer's, and every loop in this package that walks a table's
    records goes through here.

    Without it, a disc whose header overstates its own live count hands
    `range(num_records)` an `IndexError` from `read_record`. The source absorbed
    that with a per-record `except Exception: continue`, which dropped every
    record past the allocation silently and would have dropped any other error
    with them.

    `TDBTable.find_record` and `find_records` iterate `num_records` unbounded
    and are therefore not used anywhere in this package, for the same reason.
    """
    return range(min(table.num_records, table.capacity))


def _index_map(table: TDBTable) -> dict[int, int]:
    """`{INDX value: record position}` over one table's live records.

    Positive `INDX` values only, which is the source's filter: zero is what an
    unused row holds, and mapping it would make every unused row in the table
    look like the same player. Later records win a tie, also the source's
    behaviour, and it matters because nothing guarantees `INDX` is unique.
    """
    result: dict[int, int] = {}
    for i in _live_records(table):
        value = table.read_record(i).get("INDX")
        if isinstance(value, int) and value > 0:
            result[value] = i
    return result


def _play_id_by_indx(table: TDBTable) -> dict[int, int]:
    """`{PLAY.INDX: PLAY.ID__}`, the middle hop of the record chain.

    Unlike `_index_map` this does **not** drop `INDX` of zero, because the
    source did not: a PLAY row is looked up by a ROST row's `INDX`, and if both
    are zero the source paired them. A row whose `ID__` is zero is then dropped
    downstream anyway, by `_index_map`'s positive filter on SPBT.

    A table with no `INDX` field at all collapses to the single key -1, which is
    the source's `rec.get("INDX", -1)` and is unreachable from a ROST row --
    `INDX` is unsigned there, so no ROST row can name it.
    """
    result: dict[int, int] = {}
    for i in _live_records(table):
        record = table.read_record(i)
        key = record.get("INDX", -1)
        value = record.get("ID__", 0)
        if isinstance(key, int) and isinstance(value, int):
            result[key] = value
    return result


@dataclass(frozen=True)
class _RosterSlot:
    """One ROST row this patcher may write, and everything it points at.

    `spai_index` and `sgai_index` are both optional and at most one is normally
    set: a player with an SGAI row is a goalie, and that is how
    `_classify_slots` decides which pool the row belongs to.
    """

    rost_index: int
    player_id: int
    bio_index: int
    spai_index: int | None
    sgai_index: int | None


@dataclass(frozen=True)
class _MasterTables:
    """The master TDB's tables, plus the three lookups built from them once.

    Built once per patch rather than once per team: `_index_map` reads every
    live record of SPBT, SPAI and SGAI, which on a real disc is a few thousand
    records each, and there are 32 teams.
    """

    tdb: TDBFile
    rost: TDBTable
    play_id_by_indx: dict[int, int]
    spbt_by_indx: dict[int, int]
    spai_by_indx: dict[int, int]
    sgai_by_indx: dict[int, int]

    @classmethod
    def of(cls, tdb: TDBFile) -> _MasterTables:
        """Read the tables out of a master TDB, or say which are missing.

        SPBT, ROST and PLAY are required -- they are the three hops of the
        chain, and without any one of them no player can be located. SPAI and
        SGAI are not: a disc missing SGAI can still have its bios and lines
        rewritten, and every attribute write is guarded by the index being
        found.
        """
        spbt = tdb.get_table("SPBT")
        rost = tdb.get_table("ROST")
        play = tdb.get_table("PLAY")
        missing = [
            name
            for name, table in (("SPBT", spbt), ("ROST", rost), ("PLAY", play))
            if table is None
        ]
        if missing or spbt is None or rost is None or play is None:
            raise RomError(
                f"{TDB_MASTER} is missing the table(s) {', '.join(missing)}; "
                f"it holds: {', '.join(sorted(tdb.tables))}"
            )
        spai = tdb.get_table("SPAI")
        sgai = tdb.get_table("SGAI")
        return cls(
            tdb=tdb,
            rost=rost,
            play_id_by_indx=_play_id_by_indx(play),
            spbt_by_indx=_index_map(spbt),
            spai_by_indx=_index_map(spai) if spai is not None else {},
            sgai_by_indx=_index_map(sgai) if sgai is not None else {},
        )


@dataclass(frozen=True)
class _MirrorTables:
    """The second copies of the master's tables, in the two split TDB files.

    `bioatt` mirrors SPBT, SPAI and SGAI; `roster_rost` mirrors ROST. Both TDBs
    are optional, and so is any individual table inside them.

    Only `roster_rost` is held as a table rather than a file, because it is the
    only one whose write needs a capacity check here:
    `TDBTable.write_record` raises `IndexError` past the allocation, while
    `NHL07PSPRomWriter.write_player_bio` and its two siblings each test
    `record_idx >= table.capacity` themselves and return. The source wrote that
    same test a second time at every call site; it is dropped here as
    provably redundant, not as a behaviour change.
    """

    bioatt: TDBFile | None
    roster_rost: TDBTable | None

    @classmethod
    def of(cls, bioatt: TDBFile | None, roster: TDBFile | None) -> _MirrorTables:
        return cls(
            bioatt=bioatt,
            roster_rost=roster.get_table("ROST") if roster is not None else None,
        )

    def write_rost(self, index: int, values: Mapping[str, object]) -> None:
        """Mirror one ROST write, if there is a mirror with room for it."""
        if self.roster_rost is not None and index < self.roster_rost.capacity:
            self.roster_rost.write_record(index, values)


@register(
    "nhl07-psp",
    platform="psp",
    sport="hockey",
    requires_slot_mapping=False,
    providers=("espn", "nhl"),
)
class NHL07PSPPatcher(Patcher):
    """Teams map to ROM slots by abbreviation, so no manual mapping step.

    `requires_slot_mapping=False` follows the source, which has no mapping step
    and no way to express one: `map_rosters_to_nhl07` looked every fetched team
    up in `MODERN_NHL_TO_NHL07` and dropped the ones with no entry. That is the
    same shape as `nhl94-genesis` and `kgj-mlb-snes` and unlike `iss-snes`,
    which needed a mapping because its 27 slots are national teams that no club
    abbreviation names. All 32 NHL 07 slots are real teams with real
    abbreviations, including the two All-Star sides -- Seattle takes `EAS` and
    Vegas `WES`, which is arbitrary but is at least a slot each rather than
    being dropped.

    Providers: `espn` for the current season, `nhl` for seasons back to 1993.
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
        self.mapper = NHL07StatMapper()
        # Eagerly, and both clients create their cache directory from their own
        # constructor, so constructing this patcher can raise `StorageError`.
        # Nothing here reaches the network.
        #
        # `Any` for the same reason `nhl94_genesis` needs it: of the three
        # methods `fetch` calls, only `get_nhl_teams` has one signature on both
        # clients. `get_hockey_squad` takes a team id on ESPN and an
        # abbreviation on the NHL API, and `get_hockey_team_leaders` splits the
        # same way, so no single type describes both and calling the wrong
        # branch is a runtime bug rather than a mypy error. Both branches are
        # pinned by tests.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        """Inspect an ISO and list its team slots.

        Two checks, and they guard different sets of entry points on purpose.

        `NHL07PSPRomReader.validate(deep=True)` is a **heuristic**: `db.viv` is a
        BIGF, it holds `nhlbioatt.tdb`, and that decompresses to something whose
        magic is `DB\\x00\\x08`. It guards this method and NOT `patch`. A false
        positive here costs the user every EA PSP disc they own appearing as
        NHL 07 in a detection sweep; a false negative costs only auto-detection,
        because `patch --game nhl07-psp` reaches `patch` directly and
        `nhlbioatt.tdb` is a mirror -- the master TDB is what the patch actually
        needs. `tests/games/nhl07_psp/test_patcher.py` pins that asymmetry so
        nobody harmonises it later.

        `_db_viv_extent_fits` is an **arithmetic bound** and guards both. Its
        docstring has the arithmetic and the silent-corruption path it stops.

        Raises:
            RomError: the file is missing or unreadable, or it is a compressed
                disc image this patcher cannot read.
        """
        with as_rom_error(rom_path):
            compressed = _compressed_image_format(rom_path)
            if compressed is not None:
                raise RomError(
                    f"{rom_path} is a {compressed} image. This patcher reads only an "
                    f"uncompressed ISO 9660 disc image; decompress it first, for example "
                    f"with `maxcso --decompress`."
                )

            reader = NHL07PSPRomReader(str(rom_path))
            loaded = reader.load()
            size = os.path.getsize(rom_path)
            if not loaded:
                # Readable, and not this game: too small to be an ISO, no PVD,
                # or no `/PSP_GAME/USRDIR/DB/DB.VIV`. `analyze` probes every
                # registered patcher against one image, so this must not raise.
                return RomInfo(
                    path=str(rom_path),
                    size=size,
                    game_id=self.game_id,
                    is_valid=False,
                )

            info = reader.get_info(deep=True)
            is_valid = info.is_valid and _db_viv_extent_fits(rom_path, reader)

        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    # Read out of the disc's own STEA table, so two NHL 07 ISOs
                    # with different rosters render differently. Empty when
                    # STEA had no name for the slot, in which case the reader
                    # has already substituted the constant -- see
                    # `_read_team_slots`.
                    current_name=slot.name,
                    display_name=(
                        NHL07_TEAM_NAMES[slot.index]
                        if 0 <= slot.index < SLOT_COUNT
                        else f"Slot {slot.index}"
                    ),
                )
                # Only slots the game has a name for. A STEA `INDX` outside
                # 0-31 is not a team this patcher can write, and putting it in
                # the list would offer the user a slot `map_rosters` can never
                # fill.
                for slot in info.team_slots
            ],
            # ROM-derived and unreachable once the reader is gone. Both are
            # JSON-serialisable, per `core/models.py`.
            extra={
                "db_viv_size": len(reader.get_db_viv() or b""),
                "team_slot_count": len(info.team_slots),
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
        self.status("Fetching NHL teams...")
        teams = self.api.get_nhl_teams()
        if not teams:
            raise ApiError("The provider returned no NHL teams")

        # Only teams with a slot are worth fetching: the rest cost two network
        # round trips each and are then dropped by `map_rosters`.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches an NHL 07 PSP ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            if self.provider == "nhl":
                players = self.api.get_hockey_squad(team.code, season)
                leaders = self.api.get_hockey_team_leaders(team.code, season)
            else:
                # DELIBERATE DIVERGENCE: the source called both of these with no
                # season on the ESPN branch. Neither omission was harmless and
                # they were not the same omission. The squad endpoint has no
                # season in its URL but does have one in its cache key, so
                # without it the first season ever fetched was served forever;
                # the leaders endpoint takes the season as a URL path segment,
                # so its default meant a `--season 2024` run asked ESPN for a
                # different year's statistics and stapled them to the squad.
                players = self.api.get_hockey_squad(team.id, season)
                leaders = self.api.get_hockey_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # DELIBERATE DIVERGENCE: the source left these on
                    # `self.team_stats`, an instance side channel written by
                    # `fetch_rosters` and read by `map_rosters_to_nhl07` through
                    # `getattr(self, "team_stats", {})` -- so calling the two out
                    # of order, or in two processes with a rosters file between
                    # them, silently downgraded every player to position
                    # defaults instead of failing. In `extra` the whole result
                    # round-trips through JSON.
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
    ) -> MappedRosters:
        """Reduce league data to a list of `NHL07PlayerRecord` per matched slot.

        Sparse: a key exists only for a slot some fetched team mapped to.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, list[NHL07PlayerRecord]] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < SLOT_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders, max_players=MAX_PLAYERS)
            records = [
                self.mapper.map_player(player, roster.team.code, leaders.get(str(player.id), {}))
                for player in selected
            ]

            # DELIBERATE DIVERGENCE. `MODERN_NHL_TO_NHL07` collapses 38 codes
            # onto 32 slots -- `LA`/`LAK`, `NJ`/`NJD`, `SJ`/`SJS`, `TB`/`TBL`,
            # `PHX`/`ARI`/`UTA` and `ATL`/`WPG` -- so two entries in
            # `data.teams` can name one slot. The source assigned
            # `teams[slot] = nhl07_players` unconditionally. It got away with it
            # because its own `fetch_rosters` kept a dict keyed by team code and
            # stored a team only `if players:`, so an empty roster could never
            # reach the mapping step; that is the sort of protection that
            # survives exactly until someone calls `map_rosters` on a rosters
            # file. Without this guard an empty alias arriving second wipes the
            # populated record, `patch` skips the slot, and the run reports
            # success with `teams_patched` short by one and the 2006 roster
            # still on the disc.
            #
            # An empty roster that collides with nothing still takes the slot:
            # the mapped result keeps showing which slots a provider team
            # matched, and `patch` is what keeps the empty list away from the
            # writer.
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
        """Copy the ISO, rewrite the roster tables inside it, write it back.

        Raises:
            RomError: the ISO is missing, unreadable, compressed, not this game
                by the arithmetic bound, missing the master TDB or its tables,
                or too small to hold the rebuilt archive.
            MappingError: `rosters` was produced by a different patcher.
        """
        # First, ahead of every other guard and ahead of the first status
        # message: it is the one check that costs no I/O, and the failure it
        # prevents is the writer choking on another game's record type with an
        # exception outside this library's hierarchy.
        rosters.require_game(self.game_id)

        with as_rom_error(rom_path):
            compressed = _compressed_image_format(rom_path)
            if compressed is not None:
                raise RomError(
                    f"{rom_path} is a {compressed} image. This patcher reads only an "
                    f"uncompressed ISO 9660 disc image; decompress it first, for example "
                    f"with `maxcso --decompress`."
                )

            self.status("Validating ROM...")
            source = NHL07PSPRomReader(str(rom_path))
            if not source.load():
                raise RomError(f"Not a valid NHL 07 PSP ISO: {rom_path}")
            # The arithmetic bound, and NOT `validate(deep=True)`: see
            # `analyze_rom` for why only one of the two reaches this method.
            if not _db_viv_extent_fits(rom_path, source):
                start, end = _db_viv_extent(source)
                raise RomError(
                    f"Not a valid NHL 07 PSP ISO: {rom_path}: db.viv is declared at bytes "
                    f"{start}-{end} and the file is {os.path.getsize(rom_path)} bytes, so the "
                    f"archive is truncated"
                )

            self.status("Copying ISO...")
            writer = NHL07PSPRomWriter(str(rom_path), str(output_path))
            writer.copy_iso(on_progress)

            self.status("Loading db.viv...")
            if on_progress is not None:
                on_progress(PROGRESS_COPY_END, "Loading db.viv...")
            if not writer.load():
                raise RomError(f"Failed to read db.viv back from the copy at {output_path}")

            self.status("Parsing TDB tables...")
            master_tdb, bioatt_tdb, roster_tdb = self._parse_tdbs(writer)
            tables = _MasterTables.of(master_tdb)
            mirrors = _MirrorTables.of(bioatt_tdb, roster_tdb)

            self.status("Writing rosters...")
            teams_patched, players_patched = self._write_all_teams(
                writer, rosters, tables, mirrors, on_progress
            )

            self.status("Rebuilding db.viv...")
            writer.rebuild_and_write(
                self._archive_spelling(writer, master_tdb, bioatt_tdb, roster_tdb),
                on_progress,
            )

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    # -- patch helpers ------------------------------------------------------

    def _parse_tdbs(
        self, writer: NHL07PSPRomWriter
    ) -> tuple[TDBFile, TDBFile | None, TDBFile | None]:
        """The master TDB and its two optional mirrors.

        Only the master is required. `nhlbioatt.tdb` and `nhlrost.tdb` hold
        second copies of tables the master already has, so a disc without them
        is patchable; a disc without `nhl2007.tdb` is not, and the error names
        what the archive does hold, because "master TDB not found" on its own
        leaves a user with no way to tell a wrong disc from a wrong constant.
        """
        reader = writer.reader
        if reader is None:
            raise RomError("db.viv was never loaded")
        master_tdb = reader.get_tdb(TDB_MASTER)
        if master_tdb is None:
            viv = writer.db_viv or b""
            names = [entry.name for entry in bigf_parse(viv)] if viv else []
            raise RomError(f"db.viv holds no {TDB_MASTER}; its files are: {', '.join(names)}")
        return master_tdb, reader.get_tdb(TDB_BIOATT), reader.get_tdb(TDB_ROSTER)

    @staticmethod
    def _archive_spelling(
        writer: NHL07PSPRomWriter,
        master_tdb: TDBFile,
        bioatt_tdb: TDBFile | None,
        roster_tdb: TDBFile | None,
    ) -> dict[str, TDBFile]:
        """Map each modified TDB to the name the archive itself uses for it.

        `bigf_replace_inplace` selects its target case-insensitively and this is
        not why the spelling is read back -- `bigf_replace`, which does check
        membership case-sensitively after folding case to select, is the trap
        that made both NHL patchers do this. Keeping it means the returned
        mapping is also what a reader of the progress messages sees, and those
        should say what is on the disc.
        """
        entries = bigf_parse(writer.db_viv or b"")
        names = {TDB_MASTER: TDB_MASTER, TDB_BIOATT: TDB_BIOATT, TDB_ROSTER: TDB_ROSTER}
        for entry in entries:
            for wanted in names:
                if entry.name.lower() == wanted.lower():
                    names[wanted] = entry.name

        modified = {names[TDB_MASTER]: master_tdb}
        if bioatt_tdb is not None:
            modified[names[TDB_BIOATT]] = bioatt_tdb
        if roster_tdb is not None:
            modified[names[TDB_ROSTER]] = roster_tdb
        return modified

    def _write_all_teams(
        self,
        writer: NHL07PSPRomWriter,
        rosters: MappedRosters,
        tables: _MasterTables,
        mirrors: _MirrorTables,
        on_progress: ProgressFn | None,
    ) -> tuple[int, int]:
        """Write every mapped slot, returning (teams patched, players patched).

        The slot range is re-checked here as well as in `map_rosters`, because
        the keys come from a plain dict that may have crossed a JSON boundary
        since. An out-of-range slot would find no ROST records and write
        nothing, so the guard costs nothing to hold and the alternative is a
        number in `teams_patched` for a team that does not exist.
        """
        targets = sorted(
            slot for slot, players in rosters.teams.items() if 0 <= slot < SLOT_COUNT and players
        )
        teams_patched = 0
        players_patched = 0
        span = PROGRESS_RECORDS_END - PROGRESS_COPY_END

        for i, slot in enumerate(targets):
            players: list[NHL07PlayerRecord] = rosters.teams[slot]
            if on_progress is not None:
                on_progress(
                    PROGRESS_COPY_END + (i / len(targets)) * span,
                    f"Writing {NHL07_TEAM_NAMES[slot]} ({len(players)} players)...",
                )
            written = self._write_team(writer, slot, players, tables, mirrors)
            players_patched += written
            # DELIBERATE DIVERGENCE: the source incremented `teams_patched` for
            # every slot it looked at, including one whose ROST rows it could
            # not match to a single player. `core/models.py` defines
            # `teams_patched` as slots something reached the ROM for, and for
            # this game the only thing written per slot is player records -- so
            # a slot that placed none of them did not get patched.
            if written > 0:
                teams_patched += 1

        return teams_patched, players_patched

    def _write_team(
        self,
        writer: NHL07PSPRomWriter,
        slot: int,
        players: list[NHL07PlayerRecord],
        tables: _MasterTables,
        mirrors: _MirrorTables,
    ) -> int:
        """Write one team's players into the rows the disc already has for it.

        Records are never created, only overwritten. The disc's own ROST rows
        for this team decide how many players it can hold and which of them are
        goalies, and a goalie is placed only in a row whose existing occupant
        has an SGAI record -- otherwise his save ratings would have nowhere to
        go. So a team the disc carried with two goalies takes exactly two,
        however many the provider returned, and the twenty-fifth player of a
        twenty-three-row team is dropped.

        Returns the number of players written, which is
        `min(goalies, goalie rows) + min(skaters, skater rows)`.
        """
        team_rows, goalie_slots, skater_slots = self._classify_slots(slot, tables)

        pairs: list[tuple[NHL07PlayerRecord, _RosterSlot]] = []
        for pool, available in (
            ([p for p in players if p.is_goalie], goalie_slots),
            ([p for p in players if not p.is_goalie], skater_slots),
        ):
            pairs.extend(zip(pool, available, strict=False))

        # Flags are generated for the players that actually got a row, in the
        # order they were paired: goalies first, then skaters. Generating them
        # from `players` instead would number the lines from a roster that
        # includes people who were never written.
        all_line_flags = self.mapper.generate_team_line_flags([p for p, _ in pairs])

        used: set[int] = set()
        for position, (player, roster_slot) in enumerate(pairs):
            used.add(roster_slot.rost_index)
            self._write_player(writer, player, roster_slot, tables, mirrors)

            line_flags = all_line_flags[position] if position < len(all_line_flags) else {}
            # The first paired player is captain and the next two are
            # alternates. That is position in the *paired* list, and goalies
            # come first, so the starting goalie wears the C. Unusual on a real
            # team, and what the source did.
            values = writer.roster_values(
                jersey=player.jersey_number,
                captain=2 if position == 0 else (1 if position in (1, 2) else 0),
                dressed=1,
                line_flags=line_flags,
            )
            tables.rost.write_record(roster_slot.rost_index, values)
            mirrors.write_rost(roster_slot.rost_index, values)

        # Every remaining row of this team is undressed, so a 2006 player cannot
        # take the ice beside a 2025 one. `team_rows` and not the two classified
        # lists: a row whose chain to a bio is broken could not be written, but
        # it is still one of this team's rows and the game would still dress it.
        for rost_index in team_rows:
            if rost_index in used:
                continue
            tables.rost.write_record(rost_index, {"DRES": 0})
            mirrors.write_rost(rost_index, {"DRES": 0})

        return len(pairs)

    @staticmethod
    def _classify_slots(
        slot: int, tables: _MasterTables
    ) -> tuple[list[int], list[_RosterSlot], list[_RosterSlot]]:
        """This team's ROST rows: all of them, then the goalie and skater rows.

        A row reaches one of the two classified lists only if its `INDX` names a
        PLAY record and that record's `ID__` names an SPBT record. A row that
        fails either is still in the first list, because it is still this team's
        row and still has to be undressed.

        `TDBTable.find_records` would answer the first list and is deliberately
        not used: it iterates `num_records` without bounding it by `capacity`,
        which `formats/ea_tdb.py` documents as the caller's job. See
        `_live_records`.
        """
        team_rows: list[int] = []
        goalie_slots: list[_RosterSlot] = []
        skater_slots: list[_RosterSlot] = []

        for rost_index in _live_records(tables.rost):
            record = tables.rost.read_record(rost_index)
            if record.get("TEAM") != slot:
                continue
            team_rows.append(rost_index)

            rost_indx = record.get("INDX")
            if not isinstance(rost_indx, int):
                continue
            player_id = tables.play_id_by_indx.get(rost_indx)
            if player_id is None:
                continue
            bio_index = tables.spbt_by_indx.get(player_id)
            if bio_index is None:
                continue

            roster_slot = _RosterSlot(
                rost_index=rost_index,
                player_id=player_id,
                bio_index=bio_index,
                spai_index=tables.spai_by_indx.get(player_id),
                sgai_index=tables.sgai_by_indx.get(player_id),
            )
            if roster_slot.sgai_index is not None:
                goalie_slots.append(roster_slot)
            else:
                skater_slots.append(roster_slot)

        return team_rows, goalie_slots, skater_slots

    @staticmethod
    def _write_player(
        writer: NHL07PSPRomWriter,
        player: NHL07PlayerRecord,
        roster_slot: _RosterSlot,
        tables: _MasterTables,
        mirrors: _MirrorTables,
    ) -> None:
        """Write one player's bio and attributes to the master and to the mirror.

        No capacity check here: `write_player_bio`, `write_skater_attrs` and
        `write_goalie_attrs` each test the index against the table's own
        capacity and return without writing, so a mirror smaller than the master
        simply does not receive the record.
        """
        writer.write_player_bio(tables.tdb, roster_slot.bio_index, player)
        if mirrors.bioatt is not None:
            writer.write_player_bio(mirrors.bioatt, roster_slot.bio_index, player)

        if player.is_goalie and player.goalie_attrs is not None:
            if roster_slot.sgai_index is not None:
                writer.write_goalie_attrs(tables.tdb, roster_slot.sgai_index, player.goalie_attrs)
                if mirrors.bioatt is not None:
                    writer.write_goalie_attrs(
                        mirrors.bioatt, roster_slot.sgai_index, player.goalie_attrs
                    )
        elif player.skater_attrs is not None:
            if roster_slot.spai_index is not None:
                writer.write_skater_attrs(tables.tdb, roster_slot.spai_index, player.skater_attrs)
                if mirrors.bioatt is not None:
                    writer.write_skater_attrs(
                        mirrors.bioatt, roster_slot.spai_index, player.skater_attrs
                    )
