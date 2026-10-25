"""NHL 07 (PSP) on the unified Patcher interface.

Every write addresses a named TDB record field, never a byte offset: a mistyped
four-character field name is silently ignored by `TDBTable.write_record` and a
wrong record index overwrites a different real player. Neither crashes.

A team slot's records are reached through a four-hop chain, and only the last
hop is a position. For a team slot `t`:

    ROST.find_records("TEAM", t)  ->  a list of ROST record positions
    ROST[i]["INDX"]               ->  a PLAY record's INDX value
    PLAY[...]["ID__"]             ->  a player id
    SPBT / SPAI / SGAI            ->  the record whose INDX is that player id

Classify a slot as a goalie slot by whether its player id has an SGAI entry, not
by the position in the disc's bio: the attributes need a table with a row for
him.

The writes are mirrored across three TDB files. `nhl2007.tdb` is the master and
holds every table; `nhlbioatt.tdb` holds a second copy of SPBT/SPAI/SGAI and
`nhlrost.tdb` a second copy of ROST. Mirror every master write at the same
index, when the mirror has one.

`analyze_rom` and `patch` deliberately do not apply the same checks; do not
harmonise them. See `analyze_rom`.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.concurrency import parallel_map
from ...core.errors import ApiError, CapabilityError, RomError, as_rom_error
from ...core.headshots import load_headshot, measure_faces
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
from ...formats.ea_tdb import EaTdbError, TDBFile, TDBTable, bigf_parse
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.models import League, LeagueData, TeamRoster
from ...sports.nhl import NhlApiClient
from . import looks
from .models import (
    NHL07_TEAM_NAMES,
    SLOT_COUNT,
    TDB_BIOATT,
    TDB_MASTER,
    TDB_ROSTER,
    NHL07PlayerRecord,
)
from .portraits import (
    PORTAPT_PATH,
    SILHOUETTE_ART,
    headshot_portrait,
    portrait_targets,
    read_portrait,
    repack_portraits,
)
from .rom_reader import ISO_SECTOR_SIZE, NHL07PSPRomReader
from .rom_writer import (
    LINE_FLAGS,
    PROGRESS_COPY_END,
    PROGRESS_RECORDS_END,
    NHL07PSPRomWriter,
    iso_entry,
    sync_header_cache,
    write_in_place,
)
from .screens import (
    IMAGE_SLOT_NAMES,
    SLOTS,
    SLOTS_BY_NAME,
    Slot,
    plan_writes,
    read_archives,
    read_picture,
    slot_archives,
)
from .stat_mapper import MAX_PLAYERS, NHL07StatMapper
from .strings import (
    FMISC_VIV_PATH,
    LOCAL_BH_NAME,
    LOCAL_VIV_PATH,
    StringTableError,
    replace_strings,
)

# Magic of the four compressed PSP disc containers, and the name to put in the
# error. Each wraps an ISO 9660 image; refuse them by name rather than
# reporting a good backup as "not NHL 07". Do not add a decoder: `patch`
# overwrites db.viv in place at `db_lba * 2048`, which a block-compressed
# container cannot accept without a writer for that container.
_COMPRESSED_IMAGE_MAGIC = {
    b"CISO": "CSO",
    b"ZISO": "ZSO",
    b"JISO": "JSO",
    b"DAX\x00": "DAX",
}

_MAGIC_LENGTH = 4


def _compressed_image_format(rom_path: Path) -> str | None:
    """The name of the compressed-image format this file is, or None.

    Raises:
        OSError: the file is missing or unreadable. Both callers sit inside an
            `as_rom_error` block, which turns that into `RomError`.
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


@dataclass(frozen=True)
class _MirrorTables:
    """The second copies of the master's tables, in the two split TDB files.

    `bioatt` mirrors SPBT, SPAI and SGAI; `roster_rost` mirrors ROST. Both TDBs
    are optional, and so is any individual table inside them.

    `roster_rost` is held as a table because its write is the only one needing a
    capacity check here: `TDBTable.write_record` raises `IndexError` past the
    allocation, while `NHL07PSPRomWriter.write_player_bio` and its two siblings
    test `record_idx >= table.capacity` themselves and return.
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

    All 32 slots are real teams with real abbreviations, including the two
    All-Star sides: Seattle takes `EAS` and Vegas `WES`.

    Providers: `espn` for the current season, `nhl` for seasons back to 1993.
    """

    #: Screen art `patch(images={name: png})` can replace; `export_images`
    #: writes the disc's current art for each. See `screens.SLOTS`.
    image_slots: tuple[str, ...] = IMAGE_SLOT_NAMES

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
        self._transport = transport
        # `Any`: `get_hockey_squad` takes a team id on ESPN and an abbreviation
        # on the NHL API, and `get_hockey_team_leaders` splits the same way, so
        # no single type describes both clients.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        """Inspect an ISO and list its team slots.

        `NHL07PSPRomReader.validate(deep=True)` is a heuristic -- `db.viv` is a
        BIGF, holds `nhlbioatt.tdb`, and that decompresses to magic `DB\\x00\\x08`
        -- and must keep guarding this method and NOT `patch`: `nhlbioatt.tdb`
        is only a mirror, and `patch --game nhl07-psp` needs the master TDB.
        `_db_viv_extent_fits` is arithmetic and guards both.

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
                        NHL07_TEAM_NAMES[slot.index]
                        if 0 <= slot.index < SLOT_COUNT
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
            raise ApiError("No fetched team matches an NHL 07 PSP ROM slot")

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

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        """Write each slot's current art as `<out_dir>/<slot>.png`, to edit and feed back."""
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        out = Path(out_dir)
        written = []
        with as_rom_error(rom_path), open(rom_path, "rb") as f:
            archives = read_archives(f, slot_archives())
            out.mkdir(parents=True, exist_ok=True)
            for slot in SLOTS:
                picture = self._first_picture(archives, slot)
                if picture is None:
                    continue
                target = out / f"{slot.name}.png"
                picture.save(target)
                written.append(target)
        return written

    @staticmethod
    def _first_picture(archives: Mapping[str, bytes], slot: Slot) -> Any:
        for copy in slot.copies:
            archive = archives.get(copy.archive)
            if archive is None:
                continue
            for entry in bigf_parse(archive):
                if entry.name.lower() == copy.member.lower():
                    picture = read_picture(archive[entry.offset : entry.offset + entry.size], copy)
                    if picture is not None:
                        return picture
        return None

    def _collect_images(self, given: Any) -> dict[str, bytes]:
        """Screen art to write: `patch(images=...)`, slot name to PNG path or bytes."""
        images: dict[str, bytes] = {}
        for name, png in dict(given or {}).items():
            if name not in SLOTS_BY_NAME:
                raise CapabilityError(
                    f"Unknown image slot {name!r}. Slots: {', '.join(IMAGE_SLOT_NAMES)}"
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

    @staticmethod
    def _collect_texts(given: Any) -> dict[str, str | Mapping[str, str]]:
        """Front-end strings to write, from `patch(texts=...)`; none by default.

        EA's notice stays as the disc has it unless asked: the title shows
        `$EA_COPYRIGHT` in a word-wrapped field 225 px wide with room for two
        lines, which EA's two already fill (seen in PPSSPP).

        Raises:
            CapabilityError: a string id that is not plain ASCII.
        """
        texts: dict[str, str | Mapping[str, str]] = dict(given or {})
        for string_id in texts:
            if not string_id.isascii():
                raise CapabilityError(f"String ids are ASCII, like EA_COPYRIGHT: {string_id!r}")
        return texts

    def _write_images(self, writer: NHL07PSPRomWriter, pictures: Mapping[str, Any]) -> None:
        """Draw each given slot into every copy of it, in place. See `screens`."""
        with open(writer.output_path, "r+b") as f:
            archives = read_archives(f, slot_archives(set(pictures)))
            edited, written, skipped = plan_writes(archives, pictures)
            for path, data in edited.items():
                found = iso_entry(f, path)
                if found is not None:
                    f.seek(found[0].extent.offset)
                    f.write(data)
        for name, count in written.items():
            self.status(f"Screen image {name}: {count} copies")
        for name in skipped:
            self.status(f"Screen image {name} skipped: it does not fit every copy's slot")

    def _write_texts(
        self, writer: NHL07PSPRomWriter, texts: Mapping[str, str | Mapping[str, str]]
    ) -> None:
        """Replace front-end strings in `FE/LOCAL.VIV` and its cached directory.

        `texts` maps a string id (`EA_COPYRIGHT`, `LICENSE_NHL`, ...) to one text
        or to `{language: text}` for en, fr and de. See `strings`.

        Raises:
            RomError: the rebuilt `LOCAL.VIV`, or `FMISC.VIV` holding its cached
                directory, does not fit before the next file.
        """
        with open(writer.output_path, "r+b") as f:
            found = iso_entry(f, LOCAL_VIV_PATH)
            if found is None:
                return
            f.seek(found[0].extent.offset)
            old = f.read(found[0].extent.size)
            try:
                new, changed = replace_strings(old, texts)
            except (EaTdbError, StringTableError) as e:
                self.status(f"Front-end text skipped: {e}")
                return
            if changed:
                write_in_place(f, *found, new)
                sync_header_cache(f, FMISC_VIV_PATH, LOCAL_BH_NAME, old, new)
        if changed:
            self.status(f"Front-end strings replaced: {changed}")

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

            # `MODERN_NHL_TO_NHL07` collapses 39 codes onto 32 slots --
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
            RomError: the ISO is missing, unreadable, compressed, not this game
                by the arithmetic bound, missing the master TDB or its tables,
                or too small to hold the rebuilt archive.
            MappingError: `rosters` was produced by a different patcher.
        """
        rosters.require_game(self.game_id)
        # Every option is checked before the disc is touched, so a bad picture or
        # string id never leaves a half-written output behind.
        pictures = self._decode_images(self._collect_images(options.get("images")))
        texts = self._collect_texts(options.get("texts"))
        # Looks first, on a copy: the head shots are measured before the
        # multi-hundred-MB copy, so a bad network fails fast and the caller's
        # records are left as mapped.
        rosters = self._dress(rosters, bool(options.get("looks", True)))

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
            # `analyze_rom`.
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
            photos: dict[int, str] = {}
            teams_patched, players_patched = self._write_all_teams(
                writer, rosters, tables, mirrors, on_progress, photos
            )

            self.status("Rebuilding db.viv...")
            writer.rebuild_and_write(
                self._archive_spelling(writer, master_tdb, bioatt_tdb, roster_tdb),
                on_progress,
            )

            if options.get("portraits", True):
                self._write_portraits(writer, master_tdb, photos)

            if pictures:
                self._write_images(writer, pictures)

            if texts:
                self._write_texts(writer, texts)

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    def _dress(self, rosters: MappedRosters, measure: bool) -> MappedRosters:
        """A copy of `rosters` with every player's `skin` set (`looks.dress`):
        from his head shot when `measure`, else from the no-photo pick alone,
        with no download at all."""
        urls = {
            record.player_id: record.photo_url
            for players in rosters.teams.values()
            for record in players
            if record.player_id and record.photo_url
        }
        faces = measure_faces(self.cache_dir, urls, self._transport, self.status) if measure else {}
        dressed = deepcopy(rosters)
        for players in dressed.teams.values():
            looks.dress(players, faces)
        return dressed

    def _parse_tdbs(
        self, writer: NHL07PSPRomWriter
    ) -> tuple[TDBFile, TDBFile | None, TDBFile | None]:
        """The master TDB and its two optional mirrors.

        `nhlbioatt.tdb` and `nhlrost.tdb` hold second copies of tables the master
        already has, so a disc without them is still patchable; a disc without
        `nhl2007.tdb` is not.
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

        The keys become `rebuild_and_write`'s progress messages, so a message
        about `db.viv` names the file as the disc spells it. The case fold is
        not load-bearing: `bigf_replace_inplace` already selects
        case-insensitively.
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
        photos: dict[int, str],
    ) -> tuple[int, int]:
        """Write every mapped slot, returning (teams patched, players patched).

        `photos` collects each written bio record's head shot URL, "" for none.

        Re-check the slot range here as well as in `map_rosters`: the keys come
        from a plain dict that may have crossed a JSON boundary since.
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
            written = self._write_team(writer, slot, players, tables, mirrors, photos)
            players_patched += written
            # A slot that placed no player record did not get patched.
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
        photos: dict[int, str],
    ) -> int:
        """Write one team's players into the rows the disc already has for it.

        Records are never created, only overwritten. The disc's own ROST rows
        decide how many players a team holds and which are goalies; a goalie may
        only take a row whose occupant has an SGAI record, or his save ratings
        have nowhere to go. Returns
        `min(goalies, goalie rows) + min(skaters, skater rows)`.
        """
        team_rows, goalie_slots, skater_slots = self._classify_slots(slot, tables)

        pairs: list[tuple[NHL07PlayerRecord, _RosterSlot]] = []
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
            self._write_player(writer, player, roster_slot, tables, mirrors)
            photos[roster_slot.bio_index] = player.photo_url

            line_flags = all_line_flags[position] if position < len(all_line_flags) else {}
            values = writer.roster_values(
                jersey=player.jersey_number,
                dressed=int(dressed[position]),
                line_flags=line_flags,
            )
            tables.rost.write_record(roster_slot.rost_index, values)
            mirrors.write_rost(roster_slot.rost_index, values)

        # Every remaining row undressed and off every line, so a stale player
        # can neither take the ice nor hold a unit. `team_rows` and not the
        # classified lists: a row whose chain to a bio is broken is still one of
        # this team's rows and the game would dress it.
        cleared: dict[str, object] = {"DRES": 0, **dict.fromkeys(LINE_FLAGS, 0)}
        for rost_index in team_rows:
            if rost_index in used:
                continue
            tables.rost.write_record(rost_index, cleared)
            mirrors.write_rost(rost_index, cleared)

        return len(pairs)

    def _write_portraits(
        self, writer: NHL07PSPRomWriter, master_tdb: TDBFile, photos: Mapping[int, str]
    ) -> int:
        """Put each written player's head shot in his portrait, or the silhouette.

        A portrait is the entry `ART_` names in `FE/PORTAPT.VIV`. Only a bio
        whose `ART_` no other bio shares is drawn: the disc gives the same id to
        dozens of European players. A player without a head shot, or whose head
        shot fails to download or decode, gets entry 0000, the game's own
        silhouette, so no 2006 face stays on a new player. Needs Pillow; skipped
        with a status line without it, when there are portraits to draw.
        Returns how many portraits went in.
        """
        spbt = master_tdb.get_table("SPBT")
        if spbt is None or not photos:
            return 0
        art_of = {i: spbt.read_record(i).get("ART_") for i in _live_records(spbt)}
        wanted = portrait_targets(art_of, photos)
        if not wanted:
            return 0
        if importlib.util.find_spec("PIL") is None:
            self.status("Portraits skipped: Pillow is not installed")
            return 0

        with open(writer.output_path, "rb") as f:
            found = iso_entry(f, PORTAPT_PATH)
            if found is None:
                return 0
            f.seek(found[0].extent.offset)
            archive = f.read(found[0].extent.size)

        self.status(f"Drawing {len(wanted)} portraits...")
        silhouette = read_portrait(archive, SILHOUETTE_ART)
        urls = sorted({url for url in wanted.values() if url})
        heads = dict(zip(urls, parallel_map(self._load_headshot, urls), strict=True))
        images = {}
        for art, url in wanted.items():
            image = self._portrait_from(heads.get(url)) or silhouette
            if image is not None:
                images[art] = image
        new_archive, drawn = repack_portraits(archive, images)
        if drawn:
            writer.write_portraits(archive, new_archive)
        self.status(f"Portraits drawn: {drawn}")
        return drawn

    @staticmethod
    def _portrait_from(png: bytes | None) -> Any:
        if not png:
            return None
        try:
            return headshot_portrait(png)
        except Exception:  # noqa: BLE001 - Pillow's errors (a decompression bomb among them) share no base
            return None

    def _load_headshot(self, url: str) -> bytes | None:
        """`core.headshots.load_headshot` with this patcher's cache and transport:
        None, and the silhouette, when there is no usable picture."""
        return load_headshot(self.cache_dir, url, self._transport)

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
        writer: NHL07PSPRomWriter,
        player: NHL07PlayerRecord,
        roster_slot: _RosterSlot,
        tables: _MasterTables,
        mirrors: _MirrorTables,
    ) -> None:
        """Write one player's bio and attributes to the master and to the mirror.

        No capacity check here: `write_player_bio`, `write_skater_attrs` and
        `write_goalie_attrs` each test the index against the table's capacity and
        return without writing.
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
