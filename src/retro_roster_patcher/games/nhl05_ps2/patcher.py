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

import importlib.util
import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.assets import crest_png
from ...core.concurrency import parallel_map
from ...core.errors import ApiError, CapabilityError, RomError, as_rom_error
from ...core.headshots import load_headshot
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
from ..nhl07_psp.rom_writer import iso_entry, write_in_place
from . import logos, portraits, screens
from .models import (
    NAMED_SLOT_COUNT,
    NHL05_TEAM_NAMES,
    PATCHABLE_SLOT_COUNT,
    TDB_MASTER,
    TDB_ROSTER,
    NHL05PlayerRecord,
)
from .rom_reader import ISO_SECTOR_SIZE, NHL05PS2RomReader
from .rom_writer import (
    LINE_FLAGS,
    PROGRESS_COPY_END,
    PROGRESS_RECORDS_END,
    NHL05PS2RomWriter,
)
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
    #: Bio records of the 2004 players whose rows were dropped.
    dropped: list[int] = field(default_factory=list)

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

    #: Screen art `patch(images={name: png})` can replace; `export_images`
    #: writes the disc's current art for each. See `screens.SLOTS`.
    image_slots: tuple[str, ...] = screens.IMAGE_SLOT_NAMES

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
        self._transport = transport
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
        """`select_roster`'s order with the starting six first: the first G, C,
        LW and RW and the first two D. The ROST output is the same either way,
        since lines and dressing are taken per position in list order.
        """
        leaders = team_roster.extra.get("leaders") or {}
        ordered = self.mapper.select_roster(team_roster.players, leaders, max_players=MAX_PLAYERS)
        six = [
            p
            for code, n in (("G", 1), ("C", 1), ("LW", 1), ("RW", 1), ("D", 2))
            for p in [q for q in ordered if q.position == code][:n]
        ]
        return self._append_unused(self._append_unused(six, ordered), team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        order_as_given: bool = False,
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
            # `order_as_given`: the writer splits goalies from skaters itself and
            # keeps list order within each, so the caller's list goes as is.
            selected = (
                roster.players[:MAX_PLAYERS]
                if order_as_given
                else self.mapper.select_roster(roster.players, leaders, max_players=MAX_PLAYERS)
            )
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
        pictures = self._decode_images(self._collect_images(options.get("images")))

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

            if options.get("portraits", True):
                self._lend_portraits(writer, tables)

            self.status("Rebuilding DB.VIV...")
            writer.rebuild_and_write(
                self._archive_spelling(writer, master_tdb, roster_tdb),
                on_progress,
            )
            if options.get("portraits", True):
                self._write_portraits(writer, master_tdb)
            if options.get("logos", True):
                self._write_logos(writer, rosters)
            if pictures:
                self._write_images(writer, pictures)

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    def _write_portraits(self, writer: NHL05PS2RomWriter, master_tdb: TDBFile) -> int:
        """Each written player's head shot in his portrait (`portraits`), the
        silhouette without one, so no 2004 face stays on a new player. Needs
        Pillow. Returns how many portraits went in."""
        spbt = master_tdb.get_table("SPBT")
        if spbt is None or not writer.photos:
            return 0
        art_of = {i: spbt.read_record(i).get("ART_") for i in _live_records(spbt)}
        wanted = portraits.portrait_targets(art_of, writer.photos)
        if not wanted:
            return 0
        if importlib.util.find_spec("PIL") is None:
            self.status("Portraits skipped: Pillow is not installed")
            return 0
        with open(writer.output_path, "r+b") as f:
            found = iso_entry(f, portraits.PORTAPT_PATH)
            if found is None:
                return 0
            entry, next_lba = found
            f.seek(entry.extent.offset)
            archive = f.read(entry.extent.size)
            self.status(f"Drawing {len(wanted)} portraits...")
            silhouette = portraits.read(archive, portraits.SILHOUETTE_ART)
            urls = sorted({url for url in wanted.values() if url})
            heads = dict(zip(urls, parallel_map(self._headshot, urls), strict=True))
            images = {}
            for art, url in wanted.items():
                image = None
                png = heads.get(url)
                if png is not None:
                    try:
                        image = portraits.crop(png)
                    except Exception:  # noqa: BLE001 - a bad photo takes the silhouette
                        image = None
                image = image or silhouette
                if image is not None:
                    images[art] = image
            new_archive, drawn = portraits.repack(archive, images)
            if drawn:
                write_in_place(f, entry, next_lba, new_archive)
        self.status(f"Portraits drawn: {drawn}")
        return drawn

    @staticmethod
    def _lend_portraits(writer: NHL05PS2RomWriter, tables: _MasterTables) -> None:
        """Give each written player whose `ART_` has no portrait of his own (not
        in `PORTAPT.VIV`, shared, or the silhouette) the one a dropped 2004
        player leaves free; the dropped player takes the silhouette. The archive
        has no room for new entries."""
        spbt = tables.tdb.get_table("SPBT")
        if spbt is None or not writer.photos:
            return
        with open(writer.output_path, "rb") as f:
            found = iso_entry(f, portraits.PORTAPT_PATH)
            if found is None:
                return
            f.seek(found[0].extent.offset)
            names = {e.name for e in bigf_parse(f.read(found[0].extent.size))}
        art_of = {i: spbt.read_record(i).get("ART_") for i in _live_records(spbt)}
        uses = Counter(art_of.values())

        def own(art: object) -> bool:
            return (
                isinstance(art, int)
                and art != portraits.SILHOUETTE_ART
                and uses[art] == 1
                and portraits.entry_name(art) in names
            )

        free = [b for b in tables.dropped if b not in writer.photos and own(art_of.get(b))]
        for bio in writer.photos:
            if not free:
                break
            if not own(art_of.get(bio)):
                lender = free.pop()
                spbt.write_record(bio, {"ART_": art_of[lender]})
                spbt.write_record(lender, {"ART_": portraits.SILHOUETTE_ART})

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        """Write each slot's current art as `<out_dir>/<slot>.png`, to edit and feed back."""
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        out = Path(out_dir)
        written = []
        with as_rom_error(rom_path), open(rom_path, "rb") as f:
            archives = screens.read_archives(f, screens.slot_archives())
            out.mkdir(parents=True, exist_ok=True)
            for slot in screens.SLOTS:
                picture = self._first_picture(archives, slot)
                if picture is None:
                    continue
                target = out / f"{slot.name}.png"
                picture.save(target)
                written.append(target)
        return written

    @staticmethod
    def _first_picture(archives: Mapping[str, bytes], slot: screens.Slot) -> Any:
        for copy in slot.copies:
            archive = archives.get(copy.archive)
            if archive is None:
                continue
            for entry in bigf_parse(archive):
                if entry.name.lower() == copy.member.lower():
                    picture = screens.read_picture(
                        archive[entry.offset : entry.offset + entry.size], copy
                    )
                    if picture is not None:
                        return picture
        return None

    @staticmethod
    def _collect_images(given: Any) -> dict[str, bytes]:
        """Screen art to write: `patch(images=...)`, slot name to PNG path or bytes."""
        images: dict[str, bytes] = {}
        for name, png in dict(given or {}).items():
            if name not in screens.SLOTS_BY_NAME:
                raise CapabilityError(
                    f"Unknown image slot {name!r}. Slots: {', '.join(screens.IMAGE_SLOT_NAMES)}"
                )
            images[name] = Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png)
        return images

    def _decode_images(self, images: Mapping[str, bytes]) -> dict[str, Any]:
        """Each slot's picture, decoded; {} without Pillow (with a status line).

        Raises:
            CapabilityError: a picture Pillow cannot read, named by its slot.
        """
        if not images:
            return {}
        if importlib.util.find_spec("PIL") is None:
            self.status("Pillow is not installed; screen images skipped")
            return {}
        import io

        from PIL import Image

        pictures = {}
        for name, png in images.items():
            try:
                picture = Image.open(io.BytesIO(png))
                picture.load()
            except Exception as e:  # noqa: BLE001 - Pillow's errors share no base class
                raise CapabilityError(f"The {name!r} image is not a picture: {e}") from e
            pictures[name] = picture
        return pictures

    def _write_images(self, writer: NHL05PS2RomWriter, pictures: Mapping[str, Any]) -> None:
        """Draw each given slot into every copy of it, in place. See `screens`."""
        with open(writer.output_path, "r+b") as f:
            archives = screens.read_archives(f, screens.slot_archives(set(pictures)))
            edited, written, skipped = screens.plan_writes(archives, pictures)
            for path, data in edited.items():
                found = iso_entry(f, path)
                if found is not None:
                    f.seek(found[0].extent.offset)
                    f.write(data)
        for name, count in written.items():
            self.status(f"Screen image {name}: {count} copies")
        for name in skipped:
            self.status(f"Screen image {name} skipped: it does not fit every copy's slot")

    def _write_logos(self, writer: NHL05PS2RomWriter, rosters: MappedRosters) -> None:
        """Each patched club's current crest over its front-end logos (`logos`).
        Nothing is fetched for a disc without the archive or a provider
        without crests (the NHL API gives none)."""
        with open(writer.output_path, "r+b") as f:
            found = iso_entry(f, logos.ARCHIVE)
            if found is None:
                return
            entry = found[0]
            urls: dict[int, str] = {}
            for team in self.api.get_nhl_teams():
                slot = self.mapper.get_team_slot(team.code)
                if slot is not None and slot in rosters.teams and team.logo_url:
                    urls[slot] = team.logo_url
            if not urls:
                self.status("Team logos skipped: the provider has no crests")
                return
            pngs = parallel_map(
                lambda url: crest_png(None, None, url, self._transport), list(urls.values())
            )
            crests = {slot: png for slot, png in zip(urls, pngs, strict=True) if png is not None}
            f.seek(entry.extent.offset)
            edited, drawn = logos.edits(f.read(entry.extent.size), crests)
            f.seek(entry.extent.offset)
            f.write(edited)
        self.status(f"Team logos drawn: {len(drawn)} of {len(crests)} clubs")

    def _headshot(self, url: str) -> bytes | None:
        return load_headshot(self.cache_dir, url, self._transport)

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

        # From the paired players, not `players`: line numbering and dressing
        # must not count anyone who was never written.
        written = [p for p, _ in pairs]
        all_line_flags = self.mapper.generate_team_line_flags(written)
        dressed = self.mapper.dressed(written)

        used: set[int] = set()
        for position, (player, roster_slot) in enumerate(pairs):
            used.add(roster_slot.rost_index)
            self._write_player(writer, player, roster_slot, tables)

            line_flags = all_line_flags[position] if position < len(all_line_flags) else {}
            values = writer.roster_values(
                jersey=player.jersey_number,
                dressed=int(dressed[position]),
                line_flags=line_flags,
            )
            tables.rost.write_record(roster_slot.rost_index, values)
            self._mirror_rost(mirror_rost, roster_slot.rost_index, values)

        # Every row left over holds a player the provider no longer lists: it
        # moves to the highest `TEAM` the field holds (127; the disc's teams
        # run to 96), which no screen lists, undressed and off every
        # line. Not deleted: a ROST with fewer rows than the disc shipped
        # leaves the roster screens blank. `team_rows` and not the classified
        # lists: a row whose chain to a bio is broken is still one of this
        # team's rows.
        tables.dropped.extend(
            s.bio_index for s in goalie_slots + skater_slots if s.rost_index not in used
        )
        width = next((f.bit_width for f in tables.rost.fields if f.name == "TEAM"), 7)
        gone: dict[str, object] = {
            "TEAM": (1 << width) - 1,
            "DRES": 0,
            **dict.fromkeys(LINE_FLAGS, 0),
        }
        for rost_index in team_rows:
            if rost_index not in used:
                tables.rost.write_record(rost_index, gone)
                self._mirror_rost(mirror_rost, rost_index, gone)

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
        writer.photos[roster_slot.bio_index] = player.photo_url

        if player.is_goalie and player.goalie_attrs is not None:
            if roster_slot.sgai_index is not None:
                writer.write_goalie_attrs(tables.tdb, roster_slot.sgai_index, player.goalie_attrs)
        elif player.skater_attrs is not None:
            if roster_slot.spai_index is not None:
                writer.write_skater_attrs(tables.tdb, roster_slot.spai_index, player.skater_attrs)
