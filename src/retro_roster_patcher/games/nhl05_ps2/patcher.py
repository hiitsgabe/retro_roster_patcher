"""NHL 2005 (PS2) on the unified Patcher interface.

Every write addresses a named TDB record field, never a byte offset: a mistyped
four-character field name is silently ignored by `TDBTable.write_record` and a
wrong record index overwrites a different real player. Neither crashes.

A team slot's records are reached through a four-hop chain, and only the last
hop is a position. For a team slot `t`:

    ROST rows whose TEAM is t  ->  a list of ROST record positions
    ROST[i]["INDX"]            ->  a PLAY record's INDX value
    PLAY[...]["ID__"]          ->  a player id
    SPBT / SPAI / SGAI         ->  the record whose INDX is that player id

Classify a slot as a goalie slot by whether its player id has an SGAI entry, not
by the position in the disc's bio: the attributes need a table with a row for
him.

There is only one mirror, and it is ROST. `DB.VIV` holds `nhl2005.tdb`, the
master, and `nhlrost.tdb`, a second copy of ROST alone. Do not copy
`games/nhl07_psp`'s `_MirrorTables`: this game has no `nhlbioatt.tdb`, so bio and
attribute writes happen once each.

`analyze_rom` and `patch` deliberately do not apply the same checks; do not
harmonise them. See `analyze_rom`.

There is no compressed-image check, unlike `games/nhl07_psp`: a compressed image
handed here is a file with no PVD, which `NHL05PS2RomReader.load` answers with
False.
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

    The ISO 9660 directory record for `DB.VIV` declares an extent LBA and a
    length in bytes. Mode 1 sectors are 2048 bytes with no header, so the
    archive occupies

        [ lba * 2048 , lba * 2048 + size )

    and the file must be at least `lba * 2048 + size` bytes long.

    Keep this guarding both `analyze_rom` and `patch`: every layer below --
    `_extract_db_viv`, `bigf_parse`, `bigf_extract`, `refpack_decompress`,
    `TDBFile` -- accepts a truncated archive silently and `TDBFile.serialize`
    then shrinks its own output, so a short read boots to a corrupted database
    that `PatchResult` reports as a success.
    """
    start, end = _db_viv_extent(reader)
    if end == 0:
        return False
    return end <= os.path.getsize(rom_path)


def _live_records(table: TDBTable) -> range:
    """The record positions of `table` that are both live and allocated.

    `formats/ea_tdb.py` never clamps `currentRecords` to `maxRecords`, so a disc
    whose header overstates its live count raises `IndexError` from
    `read_record`. Walk every table through here; `TDBTable.find_record` and
    `find_records` iterate `num_records` unbounded and must not be used.
    """
    return range(min(table.num_records, table.capacity))


def _index_map(table: TDBTable) -> dict[int, int]:
    """`{INDX value: record position}` over one table's live records.

    Positive `INDX` only: zero is what an unused row holds, and mapping it would
    make every unused row look like the same player. `INDX` is not guaranteed
    unique, and later records win a tie.
    """
    result: dict[int, int] = {}
    for i in _live_records(table):
        value = table.read_record(i).get("INDX")
        if isinstance(value, int) and value > 0:
            result[value] = i
    return result


def _play_id_by_indx(table: TDBTable) -> dict[int, int]:
    """`{PLAY.INDX: PLAY.ID__}`, the middle hop of the record chain.

    Unlike `_index_map` this keeps `INDX` of zero: a ROST row whose `INDX` is
    zero pairs with it, and a resulting `ID__` of zero is dropped downstream by
    `_index_map`'s positive filter on SPBT. A table with no `INDX` field
    collapses to the key -1, which no ROST row can name because `INDX` is
    unsigned there.
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

    A player with an SGAI row is a goalie; at most one of `spai_index` and
    `sgai_index` is normally set.
    """

    rost_index: int
    player_id: int
    bio_index: int
    spai_index: int | None
    sgai_index: int | None


@dataclass(frozen=True)
class _MasterTables:
    """The master TDB's tables, plus the three lookups built from them once."""

    tdb: TDBFile
    rost: TDBTable
    play_id_by_indx: dict[int, int]
    spbt_by_indx: dict[int, int]
    spai_by_indx: dict[int, int]
    sgai_by_indx: dict[int, int]

    @classmethod
    def of(cls, tdb: TDBFile) -> _MasterTables:
        """Read the tables out of a master TDB, or say which are missing.

        SPBT, ROST and PLAY are the three hops of the chain and are required.
        SPAI and SGAI are not: a disc missing them can still have its bios and
        lines rewritten.
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

    All 30 patchable slots are real NHL clubs with real abbreviations, and
    `MODERN_NHL_TO_NHL05` carries both providers' spellings of each.

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
        # `Any`: `get_hockey_squad` takes a team id on ESPN and an abbreviation
        # on the NHL API, and `get_hockey_team_leaders` splits the same way, so
        # no single type describes both clients.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        """Inspect an ISO and list its team slots.

        `NHL05PS2RomReader.validate(deep=True)` is a heuristic -- `DB.VIV` is a
        BIGF, holds `nhl2005.tdb`, and that decompresses to magic `DB\\x00\\x08`
        -- and must keep guarding this method and NOT `patch`: `patch` reaches
        the same fact through `_parse_tdbs`, which names the archive's actual
        file list in its error, and it still reads a genuine disc whose master
        TDB is stored in a way `validate` does not anticipate.
        `_db_viv_extent_fits` is arithmetic and guards both.

        Raises:
            RomError: the file is missing or unreadable.
        """
        with as_rom_error(rom_path):
            reader = NHL05PS2RomReader(str(rom_path))
            loaded = reader.load()
            size = os.path.getsize(rom_path)
            if not loaded:
                # `analyze` probes every registered patcher against one image,
                # so a file that is simply not this game must not raise.
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
                    # Read out of the disc's own STEA table.
                    current_name=slot.name,
                    display_name=(
                        NHL05_TEAM_NAMES[slot.index]
                        if 0 <= slot.index < NAMED_SLOT_COUNT
                        else f"Slot {slot.index}"
                    ),
                )
                for slot in info.team_slots
            ],
            extra={
                "db_viv_size": len(reader.get_db_viv() or b""),
                "team_slot_count": len(info.team_slots),
            },
        )

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
                # Pass `season` to both: the squad endpoint has no season in its
                # URL but does have one in its cache key, and the leaders
                # endpoint takes the season as a URL path segment.
                players = self.api.get_hockey_squad(team.id, season)
                leaders = self.api.get_hockey_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    # Carry leaders in `extra`, never on an instance attribute:
                    # the whole result has to round-trip through JSON.
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

    def suggest_squad_order(self, team_roster):
        leaders = team_roster.extra.get("leaders") or {}
        ordered = self.mapper.select_roster(team_roster.players, leaders, max_players=MAX_PLAYERS)
        return self._append_unused(ordered, team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to a list of `NHL05PlayerRecord` per matched slot.

        Sparse: a key exists only for a slot some fetched team mapped to.

        Bound slots by `PATCHABLE_SLOT_COUNT`, 30, not by 32 as
        `games/nhl07_psp` does: the reader drops every STEA record past `INDX`
        29, so `analyze` and `patch` agree on the same 30 slots. Seattle and
        Vegas map to slots 30 and 31 and are dropped here.
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

            # `MODERN_NHL_TO_NHL05` collapses 39 codes onto 32 slots --
            # `LA`/`LAK`, `NJ`/`NJD`, `SJ`/`SJS`, `TB`/`TBL`, `PHX`/`ARI`/`UTA`
            # and `ATL`/`WPG` -- so an empty alias arriving second must not wipe
            # a populated record. An empty roster colliding with nothing still
            # takes the slot; `patch` keeps it away from the writer.
            if not records and teams.get(slot):
                continue
            teams[slot] = records
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
        """Copy the ISO, rewrite the roster tables inside it, write it back.

        Raises:
            RomError: the ISO is missing, unreadable, not this game by the
                arithmetic bound, missing the master TDB or its tables, or too
                small to hold the rebuilt archive.
            MappingError: `rosters` was produced by a different patcher.
        """
        rosters.require_game(self.game_id)

        with as_rom_error(rom_path):
            self.status("Validating ROM...")
            source = NHL05PS2RomReader(str(rom_path))
            if not source.load():
                raise RomError(f"Not a valid NHL 2005 PS2 ISO: {rom_path}")
            # The arithmetic bound, and NOT `validate(deep=True)`: see
            # `analyze_rom`.
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
            # The ROST mirror, held as a table because its write is the only one
            # needing a capacity check here: `TDBTable.write_record` raises
            # `IndexError` past the allocation, while `write_player_bio` and its
            # two siblings test `record_idx >= table.capacity` themselves.
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

    def _parse_tdbs(self, writer: NHL05PS2RomWriter) -> tuple[TDBFile, TDBFile | None]:
        """The master TDB and its one optional mirror.

        `nhlrost.tdb` holds a second copy of a table the master already has, so a
        disc without it is still patchable; a disc without `nhl2005.tdb` is not.
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

        The keys become `rebuild_and_write`'s progress messages, so a message
        about `DB.VIV` names the file as the disc spells it. The case fold is not
        load-bearing: `bigf_replace_inplace` already selects case-insensitively.
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

        Re-check the slot range here as well as in `map_rosters`: the keys come
        from a plain dict that may have crossed a JSON boundary since.
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
            # A slot that placed no player record did not get patched.
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
        decide how many players a team holds and which are goalies; a goalie may
        only take a row whose occupant has an SGAI record, or his save ratings
        have nowhere to go. Returns
        `min(goalies, goalie rows) + min(skaters, skater rows)`.
        """
        team_rows, goalie_slots, skater_slots = self._classify_slots(slot, tables)

        pairs: list[tuple[NHL05PlayerRecord, _RosterSlot]] = []
        for pool, available in (
            ([p for p in players if p.is_goalie], goalie_slots),
            ([p for p in players if not p.is_goalie], skater_slots),
        ):
            pairs.extend(zip(pool, available, strict=False))

        # From the paired players, not `players`: line numbering must not count
        # anyone who was never written.
        all_line_flags = self.mapper.generate_team_line_flags([p for p, _ in pairs])

        used: set[int] = set()
        for position, (player, roster_slot) in enumerate(pairs):
            used.add(roster_slot.rost_index)
            self._write_player(writer, player, roster_slot, tables)

            line_flags = all_line_flags[position] if position < len(all_line_flags) else {}
            # CAPT: 2 is the captain, 1 an alternate, 0 neither. Position in the
            # paired list, where goalies come first, so the starting goalie
            # wears the C.
            values = writer.roster_values(
                jersey=player.jersey_number,
                captain=2 if position == 0 else (1 if position in (1, 2) else 0),
                dressed=1,
                line_flags=line_flags,
            )
            tables.rost.write_record(roster_slot.rost_index, values)
            self._mirror_rost(mirror_rost, roster_slot.rost_index, values)

        # DRES 0 on every remaining row, so a stale player cannot take the ice.
        # `team_rows` and not the classified lists: a row whose chain to a bio is
        # broken is still one of this team's rows and the game would dress it.
        for rost_index in team_rows:
            if rost_index in used:
                continue
            tables.rost.write_record(rost_index, {"DRES": 0})
            self._mirror_rost(mirror_rost, rost_index, {"DRES": 0})

        return len(pairs)

    @staticmethod
    def _mirror_rost(mirror_rost: TDBTable | None, index: int, values: dict[str, object]) -> None:
        """Mirror one ROST write into `nhlrost.tdb`, if there is room for it."""
        if mirror_rost is not None and index < mirror_rost.capacity:
            mirror_rost.write_record(index, values)

    @staticmethod
    def _classify_slots(
        slot: int, tables: _MasterTables
    ) -> tuple[list[int], list[_RosterSlot], list[_RosterSlot]]:
        """This team's ROST rows: all of them, then the goalie and skater rows.

        A row reaches a classified list only if its `INDX` names a PLAY record
        and that record's `ID__` names an SPBT record. A row that fails either is
        still in the first list, because it still has to be undressed.

        Do not swap in `TDBTable.find_records`: it iterates `num_records`
        unbounded by `capacity`. See `_live_records`.
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
        SPBT, SPAI and SGAI exist only in the master.

        No capacity check here: `write_player_bio`, `write_skater_attrs` and
        `write_goalie_attrs` each test the index against the table's capacity and
        return without writing.
        """
        writer.write_player_bio(tables.tdb, roster_slot.bio_index, player)

        if player.is_goalie and player.goalie_attrs is not None:
            if roster_slot.sgai_index is not None:
                writer.write_goalie_attrs(tables.tdb, roster_slot.sgai_index, player.goalie_attrs)
        elif player.skater_attrs is not None:
            if roster_slot.spai_index is not None:
                writer.write_skater_attrs(tables.tdb, roster_slot.spai_index, player.skater_attrs)
