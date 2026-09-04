"""NHL 2005 (PS2) on the unified Patcher interface.

The translation layer between the ported reader/writer/mapper and the contracts
in `core.patcher`. Four things about this game are worth reading before the code.

**It patches named database records, not byte offsets.** There is not one
hardcoded player address in the package. Every write is
`table.write_record(idx, {"FNME": ..., "SACC": ...})` against four-character
field names whose widths and bit offsets come out of the file's own headers. So
the failure modes are different: a mistyped field name is silently ignored by
`TDBTable.write_record`, and a wrong record index writes a real player over a
different real player. Neither shows up as a crash.

**Records are reached through a four-hop chain, and only the last hop is a
position.** For a team slot `t`:

    ROST rows whose TEAM is t  ->  a list of ROST record positions
    ROST[i]["INDX"]            ->  a PLAY record's INDX value
    PLAY[...]["ID__"]          ->  a player id
    SPBT / SPAI / SGAI         ->  the record whose INDX is that player id

Nothing in that chain is the identity function. `patch` builds three
`INDX -> position` maps to walk it, and a slot is classified as a goalie slot by
whether its player id has an SGAI entry -- not by what the disc's bio says the
position is -- because the attributes have to go to a table that has a row for
him.

**There is only one mirror, and it is ROST.** `DB.VIV` holds `nhl2005.tdb`, the
master, and `nhlrost.tdb`, a second copy of ROST alone. `games/nhl07_psp` has a
third member, `nhlbioatt.tdb`, mirroring SPBT/SPAI/SGAI, and every bio and
attribute write there happens twice. Here each happens once. A port that copied
NHL 07's `_MirrorTables` wholesale would carry a `bioatt` field that is always
`None` and a pair of mirrored writes that never run.

**`analyze_rom` and `patch` do not apply the same checks**, deliberately, and
`_db_viv_extent_fits` versus `NHL05PS2RomReader.validate` is the split. See
`analyze_rom`.

**No compressed-image check, unlike `games/nhl07_psp`.** That game refuses
`.cso`/`.zso`/`.jso`/`.dax` by magic number because its upstream ROM-finder
configuration advertised `.cso` against a reader with no CSO support. Measured
for this game: the same front end configures NHL 2005 with
`file_extensions=[".iso", ".zip"]` and nothing else, so there is no advertised
capability to be honest about. A compressed image handed here is a file with no
PVD, which `NHL05PS2RomReader.load` answers with False and `analyze_rom` reports
as `is_valid=False` -- the correct answer for "not this game". Adding the check
anyway would mean shipping a comment justifying it that is false.
"""

from __future__ import annotations

import os
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
    NAMED_SLOT_COUNT,
    NHL05_TEAM_NAMES,
    PATCHABLE_SLOT_COUNT,
    TDB_MASTER,
    TDB_ROSTER,
    NHL05PlayerRecord,
)
from .rom_reader import ISO_SECTOR_SIZE, NHL05PS2RomReader
from .rom_writer import PROGRESS_COPY_END, PROGRESS_RECORDS_END, NHL05PS2RomWriter
from .stat_mapper import MAX_PLAYERS, NHL05StatMapper


def _db_viv_extent(reader: NHL05PS2RomReader) -> tuple[int, int]:
    """(first byte, last byte + 1) of `DB.VIV` as the ISO's directory declares it.

    (0, 0) when the archive cannot be located at all, which the callers treat
    the same way as an extent that does not fit.
    """
    db_lba, db_size, _ = reader.find_db_viv_location()
    if db_lba == 0:
        return 0, 0
    start = db_lba * ISO_SECTOR_SIZE
    return start, start + db_size


def _db_viv_extent_fits(rom_path: Path, reader: NHL05PS2RomReader) -> bool:
    """Does the whole of `DB.VIV` lie inside the file?

    This is the **arithmetic bound**, and unlike `NHL05PS2RomReader.validate` it
    guards `analyze_rom` *and* `patch`. The difference is what kind of claim each
    makes. `validate` guesses at meaning -- "an archive holding a file called
    `nhl2005.tdb` that decompresses to a TDB is probably NHL 2005" -- and a wrong
    guess costs a user auto-detection, which `patch --game nhl05-ps2` routes
    around. This is arithmetic on numbers the file states about itself, and a
    file that fails it provably cannot be patched, so exempting `patch` would
    preserve exactly the failure the check exists to kill.

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
    The source used `rost.find_records("TEAM", team_idx)` and that is the one
    call this replaces.
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
    records each, and there are 30 teams.
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


@register(
    "nhl05-ps2",
    platform="ps2",
    sport="hockey",
    requires_slot_mapping=False,
    providers=("espn", "nhl"),
)
class NHL05PS2Patcher(Patcher):
    """Teams map to ROM slots by abbreviation, so no manual mapping step.

    `requires_slot_mapping=False` follows the source, which has no mapping step
    and no way to express one: `map_rosters_to_nhl05` looked every fetched team
    up in `MODERN_NHL_TO_NHL05` and dropped the ones with no entry. That is the
    same shape as `nhl94-genesis` and `nhl07-psp` and unlike `iss-snes`, which
    needed a mapping because its 27 slots are national teams that no club
    abbreviation names. All 30 slots this game patches are real NHL clubs with
    real abbreviations, and the mapping table already carries both providers'
    spellings of each.

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
        self.mapper = NHL05StatMapper()
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

        `NHL05PS2RomReader.validate(deep=True)` is a **heuristic**: `DB.VIV` is a
        BIGF, it holds `nhl2005.tdb`, and that decompresses to something whose
        magic is `DB\\x00\\x08`. It guards this method and NOT `patch`.

        That split needs a different argument here from the one
        `games/nhl07_psp` makes, because that game's deep check names a *mirror*
        the patch does not need, and this one names the master TDB the patch
        cannot do without. So "a false negative costs only auto-detection" is not
        the whole reason. The rest of it is that `patch` reaches the same fact by
        a better route: `_parse_tdbs` looks the master up by name and, when it is
        absent, raises with the archive's actual file list in the message.
        Refusing earlier on the heuristic would replace that with "not a valid
        NHL 2005 PS2 ISO", and it would additionally refuse a genuine disc whose
        master TDB is stored in a way `validate` does not anticipate -- stored
        uncompressed under a compression this repository has not seen, say --
        while `patch` would have read it perfectly well.
        `tests/games/nhl05_ps2/test_patcher.py` pins the asymmetry so nobody
        harmonises it later.

        `_db_viv_extent_fits` is an **arithmetic bound** and guards both. Its
        docstring has the arithmetic and the silent-corruption path it stops.

        Raises:
            RomError: the file is missing or unreadable.
        """
        with as_rom_error(rom_path):
            reader = NHL05PS2RomReader(str(rom_path))
            loaded = reader.load()
            size = os.path.getsize(rom_path)
            if not loaded:
                # Readable, and not this game: too small to be an ISO, no PVD,
                # or no `/DB/DB.VIV`. `analyze` probes every registered patcher
                # against one image, so this must not raise.
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
                    # Read out of the disc's own STEA table, so two NHL 2005
                    # ISOs with different rosters render differently. Empty when
                    # STEA had no name for the slot, in which case the reader
                    # has already substituted the constant -- see
                    # `_read_team_slots`.
                    current_name=slot.name,
                    display_name=(
                        NHL05_TEAM_NAMES[slot.index]
                        if 0 <= slot.index < NAMED_SLOT_COUNT
                        else f"Slot {slot.index}"
                    ),
                )
                # The reader has already dropped every STEA record whose `INDX`
                # is past the club slots, so this list is the 30 the patcher can
                # write and no more.
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
            raise ApiError("No fetched team matches an NHL 2005 PS2 ROM slot")

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
                    # `fetch_rosters` and read by `map_rosters_to_nhl05` through
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
        """Reduce league data to a list of `NHL05PlayerRecord` per matched slot.

        Sparse: a key exists only for a slot some fetched team mapped to.

        **Slots are bounded by `PATCHABLE_SLOT_COUNT`, which is 30**, where
        `games/nhl07_psp` bounds by 32. The source wrote `slot >= 30` here and
        `slot >= 32` there, and it is not a slip: the reader drops every STEA
        record past `INDX` 29 as well, so `analyze` and `patch` agree on the same
        30 slots. What it does mean is that `MODERN_NHL_TO_NHL05`'s `SEA` and
        `VGK` entries -- which point at the two All-Star sides -- are dead:
        Seattle and Vegas are fetched, mapped to slots 30 and 31, and dropped
        here. `models.PATCHABLE_SLOT_COUNT` argues why that is preserved.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, list[NHL05PlayerRecord]] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < PATCHABLE_SLOT_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(roster.players, leaders, max_players=MAX_PLAYERS)
            records = [
                self.mapper.map_player(player, roster.team.code, leaders.get(str(player.id), {}))
                for player in selected
            ]

            # DELIBERATE DIVERGENCE. `MODERN_NHL_TO_NHL05` collapses 39 codes
            # onto 32 slots -- `LA`/`LAK`, `NJ`/`NJD`, `SJ`/`SJS`, `TB`/`TBL`,
            # `PHX`/`ARI`/`UTA` and `ATL`/`WPG` -- so two entries in
            # `data.teams` can name one slot. The source assigned
            # `teams[slot] = nhl05_players` unconditionally. It got away with it
            # because its own `fetch_rosters` kept a dict keyed by team code and
            # stored a team only `if players:`, so an empty roster could never
            # reach the mapping step; that is the sort of protection that
            # survives exactly until someone calls `map_rosters` on a rosters
            # file. Without this guard an empty alias arriving second wipes the
            # populated record, `patch` skips the slot, and the run reports
            # success with `teams_patched` short by one and the 2004 roster
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
            RomError: the ISO is missing, unreadable, not this game by the
                arithmetic bound, missing the master TDB or its tables, or too
                small to hold the rebuilt archive.
            MappingError: `rosters` was produced by a different patcher.
        """
        # First, ahead of every other guard and ahead of the first status
        # message: it is the one check that costs no I/O, and the failure it
        # prevents is the writer choking on another game's record type with an
        # exception outside this library's hierarchy.
        rosters.require_game(self.game_id)

        with as_rom_error(rom_path):
            self.status("Validating ROM...")
            source = NHL05PS2RomReader(str(rom_path))
            if not source.load():
                raise RomError(f"Not a valid NHL 2005 PS2 ISO: {rom_path}")
            # The arithmetic bound, and NOT `validate(deep=True)`: see
            # `analyze_rom` for why only one of the two reaches this method.
            if not _db_viv_extent_fits(rom_path, source):
                start, end = _db_viv_extent(source)
                raise RomError(
                    f"Not a valid NHL 2005 PS2 ISO: {rom_path}: DB.VIV is declared at bytes "
                    f"{start}-{end} and the file is {os.path.getsize(rom_path)} bytes, so the "
                    f"archive is truncated"
                )

            self.status("Copying ISO...")
            writer = NHL05PS2RomWriter(str(rom_path), str(output_path))
            writer.copy_iso(on_progress)

            self.status("Loading DB.VIV...")
            if on_progress is not None:
                on_progress(PROGRESS_COPY_END, "Loading DB.VIV...")
            if not writer.load():
                raise RomError(f"Failed to read DB.VIV back from the copy at {output_path}")

            self.status("Parsing TDB tables...")
            master_tdb, roster_tdb = self._parse_tdbs(writer)
            tables = _MasterTables.of(master_tdb)
            # The ROST mirror, held as a table rather than a file because it is
            # the only write that needs a capacity check here:
            # `TDBTable.write_record` raises `IndexError` past the allocation,
            # while `write_player_bio` and its two siblings each test
            # `record_idx >= table.capacity` themselves and return.
            mirror_rost = roster_tdb.get_table("ROST") if roster_tdb is not None else None

            self.status("Writing rosters...")
            teams_patched, players_patched = self._write_all_teams(
                writer, rosters, tables, mirror_rost, on_progress
            )

            self.status("Rebuilding DB.VIV...")
            writer.rebuild_and_write(
                self._archive_spelling(writer, master_tdb, roster_tdb),
                on_progress,
            )

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    # -- patch helpers ------------------------------------------------------

    def _parse_tdbs(self, writer: NHL05PS2RomWriter) -> tuple[TDBFile, TDBFile | None]:
        """The master TDB and its one optional mirror.

        Only the master is required. `nhlrost.tdb` holds a second copy of a
        table the master already has, so a disc without it is patchable; a disc
        without `nhl2005.tdb` is not, and the error names what the archive does
        hold, because "master TDB not found" on its own leaves a user with no way
        to tell a wrong disc from a wrong constant.
        """
        reader = writer.reader
        if reader is None:
            raise RomError("DB.VIV was never loaded")
        master_tdb = reader.get_tdb(TDB_MASTER)
        if master_tdb is None:
            viv = writer.db_viv or b""
            names = [entry.name for entry in bigf_parse(viv)] if viv else []
            raise RomError(f"DB.VIV holds no {TDB_MASTER}; its files are: {', '.join(names)}")
        return master_tdb, reader.get_tdb(TDB_ROSTER)

    @staticmethod
    def _archive_spelling(
        writer: NHL05PS2RomWriter,
        master_tdb: TDBFile,
        roster_tdb: TDBFile | None,
    ) -> dict[str, TDBFile]:
        """Map each modified TDB to the name the archive itself uses for it.

        **This is not load-bearing, and the migration plan says it is.** The
        plan records `bigf_replace`'s case bug -- it folds case to select the
        member and then checks membership case-sensitively -- and originally
        stated that both NHL patchers work around it by reading the archive's own
        spelling first. Measured in Phase 4a and again here: this game never
        calls `bigf_replace`. It calls `bigf_replace_inplace`, which selects
        case-insensitively and needs no workaround, and the source *imported*
        `bigf_replace` without ever using it. That unused import is not carried
        over.

        Kept anyway, and only for this: the keys of the returned mapping are
        what `rebuild_and_write` puts in its progress messages, and a message
        about `DB.VIV` should name the file as the disc spells it.
        """
        entries = bigf_parse(writer.db_viv or b"")
        names = {TDB_MASTER: TDB_MASTER, TDB_ROSTER: TDB_ROSTER}
        for entry in entries:
            for wanted in names:
                if entry.name.lower() == wanted.lower():
                    names[wanted] = entry.name

        modified = {names[TDB_MASTER]: master_tdb}
        if roster_tdb is not None:
            modified[names[TDB_ROSTER]] = roster_tdb
        return modified

    def _write_all_teams(
        self,
        writer: NHL05PS2RomWriter,
        rosters: MappedRosters,
        tables: _MasterTables,
        mirror_rost: TDBTable | None,
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
            slot
            for slot, players in rosters.teams.items()
            if 0 <= slot < PATCHABLE_SLOT_COUNT and players
        )
        teams_patched = 0
        players_patched = 0
        span = PROGRESS_RECORDS_END - PROGRESS_COPY_END

        for i, slot in enumerate(targets):
            players: list[NHL05PlayerRecord] = rosters.teams[slot]
            if on_progress is not None:
                on_progress(
                    PROGRESS_COPY_END + (i / len(targets)) * span,
                    f"Writing {NHL05_TEAM_NAMES[slot]} ({len(players)} players)...",
                )
            written = self._write_team(writer, slot, players, tables, mirror_rost)
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
        writer: NHL05PS2RomWriter,
        slot: int,
        players: list[NHL05PlayerRecord],
        tables: _MasterTables,
        mirror_rost: TDBTable | None,
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

        pairs: list[tuple[NHL05PlayerRecord, _RosterSlot]] = []
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
            self._write_player(writer, player, roster_slot, tables)

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
            self._mirror_rost(mirror_rost, roster_slot.rost_index, values)

        # Every remaining row of this team is undressed, so a 2004 player cannot
        # take the ice beside a 2025 one. `team_rows` and not the two classified
        # lists: a row whose chain to a bio is broken could not be written, but
        # it is still one of this team's rows and the game would still dress it.
        for rost_index in team_rows:
            if rost_index in used:
                continue
            tables.rost.write_record(rost_index, {"DRES": 0})
            self._mirror_rost(mirror_rost, rost_index, {"DRES": 0})

        return len(pairs)

    @staticmethod
    def _mirror_rost(mirror_rost: TDBTable | None, index: int, values: dict[str, object]) -> None:
        """Mirror one ROST write into `nhlrost.tdb`, if there is room for it.

        The source re-fetched `roster_tdb.get_table("ROST")` inside the write
        loop, once per player and again per undressed row, having already
        fetched it before the loop and tested the fetched value's capacity.
        `TDBFile.get_table` returns the same object every time, so those were
        no-ops; they are gone.
        """
        if mirror_rost is not None and index < mirror_rost.capacity:
            mirror_rost.write_record(index, values)

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
        writer: NHL05PS2RomWriter,
        player: NHL05PlayerRecord,
        roster_slot: _RosterSlot,
        tables: _MasterTables,
    ) -> None:
        """Write one player's bio and attributes into the master TDB.

        Once each, not twice: this game's `DB.VIV` has no `nhlbioatt.tdb`, so
        SPBT, SPAI and SGAI exist only in the master. `games/nhl07_psp` writes
        each of these three a second time.

        No capacity check here: `write_player_bio`, `write_skater_attrs` and
        `write_goalie_attrs` each test the index against the table's own
        capacity and return without writing.
        """
        writer.write_player_bio(tables.tdb, roster_slot.bio_index, player)

        if player.is_goalie and player.goalie_attrs is not None:
            if roster_slot.sgai_index is not None:
                writer.write_goalie_attrs(tables.tdb, roster_slot.sgai_index, player.goalie_attrs)
        elif player.skater_attrs is not None:
            if roster_slot.spai_index is not None:
                writer.write_skater_attrs(tables.tdb, roster_slot.spai_index, player.skater_attrs)
