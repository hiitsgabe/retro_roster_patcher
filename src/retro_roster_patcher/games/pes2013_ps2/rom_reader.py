"""Read a PES 2013 (PS2) ISO: locate the archives, parse the player table, the
club squads and the team-name tables of the language overlays.

    ISO 9660 -> /0_TEXT.AFS -> entry 47 -> part table -> players, squads
             -> /OVER.AFS   -> entries 5..19 -> team-name table in each overlay
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO

from ...core.errors import RomError
from ...formats import iso9660
from .afs import AfsEntry, Table, read_afs_entries, read_entry, unwrap, wrap
from .kits import KIT_ENTRY, KitTable
from .models import (
    BOOT_FILE,
    CLUB_COUNT,
    CLUB_SQUAD_SIZE,
    DATABASE_OVERLAY_ENGLISH,
    FIRST_CLUB_ENTRY,
    NATIONAL_NAMED,
    NATIONAL_SQUAD_SIZE,
    OVER_AFS,
    OVERLAY_LOAD_ADDRESS_OFFSET,
    PART_CLUB_NUMBERS,
    PART_CLUB_SQUADS,
    PART_NATIONAL_ORDER,
    PART_NATIONAL_SQUADS,
    PART_PLAYERS,
    PLAYER_ENTRY_LATIN,
    PLAYER_RECORD_SIZE,
    TEAM_ENTRY_SIZE,
    TEXT_AFS,
    TEXT_ARCHIVE_PRIMARY,
    TEXT_LANGUAGE_AFS,
    ClubSlot,
    PESRomInfo,
)

if TYPE_CHECKING:
    from .textures import Bundle

SECTOR = iso9660.SECTOR_SIZE
#: A retail image is 2.1 GB; anything under a few MB cannot hold the archives.
MIN_ISO_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class FileExtent:
    lba: int
    size: int
    #: Where the next file on the disc starts; 0 when unknown.
    next_lba: int

    @property
    def offset(self) -> int:
        return self.lba * SECTOR

    @property
    def room(self) -> int:
        """Bytes the file may grow into before the next file, or its own size."""
        return (self.next_lba - self.lba) * SECTOR if self.next_lba else self.size


@dataclass
class PlayerTables:
    """Entry 46/47 decoded: the entry's block header, its part table, and each
    part's own block header and inflated payload (`parts`)."""

    header: bytes
    table: Table
    part_headers: list[bytes]
    parts: list[bytes]

    @property
    def players(self) -> bytes:
        return self.parts[PART_PLAYERS]

    def build(self) -> bytes:
        """The entry rebuilt: every part deflated under its header, table relaid."""
        self.table.parts = [
            wrap(h, p) if p else b"" for h, p in zip(self.part_headers, self.parts, strict=True)
        ]
        return wrap(self.header, self.table.build())

    @property
    def player_count(self) -> int:
        return len(self.players) // PLAYER_RECORD_SIZE

    def record(self, index: int) -> bytes:
        start = index * PLAYER_RECORD_SIZE
        return self.players[start : start + PLAYER_RECORD_SIZE]

    def club_squad(self, club: int) -> list[int]:
        part = self.parts[PART_CLUB_SQUADS]
        start = club * CLUB_SQUAD_SIZE * 2
        return list(
            struct.unpack(f"<{CLUB_SQUAD_SIZE}H", part[start : start + CLUB_SQUAD_SIZE * 2])
        )

    def club_numbers(self, club: int) -> bytes:
        part = self.parts[PART_CLUB_NUMBERS]
        return part[club * CLUB_SQUAD_SIZE : (club + 1) * CLUB_SQUAD_SIZE]

    def national_squad(self, row: int) -> list[int]:
        part = self.parts[PART_NATIONAL_SQUADS]
        start = row * NATIONAL_SQUAD_SIZE * 2
        return list(
            struct.unpack(f"<{NATIONAL_SQUAD_SIZE}H", part[start : start + NATIONAL_SQUAD_SIZE * 2])
        )

    def set_national_squad(self, row: int, indices: list[int]) -> None:
        part = bytearray(self.parts[PART_NATIONAL_SQUADS])
        start = row * NATIONAL_SQUAD_SIZE * 2
        part[start : start + NATIONAL_SQUAD_SIZE * 2] = struct.pack(
            f"<{NATIONAL_SQUAD_SIZE}H", *indices[:NATIONAL_SQUAD_SIZE]
        )
        self.parts[PART_NATIONAL_SQUADS] = bytes(part)
        order = bytearray(self.parts[PART_NATIONAL_ORDER])
        order[row * NATIONAL_SQUAD_SIZE : (row + 1) * NATIONAL_SQUAD_SIZE] = bytes(
            range(NATIONAL_SQUAD_SIZE)
        )
        self.parts[PART_NATIONAL_ORDER] = bytes(order)

    def referenced(self) -> set[int]:
        """Every record some club or national squad points at."""
        used: set[int] = set()
        for part, _size in (
            (self.parts[PART_CLUB_SQUADS], CLUB_SQUAD_SIZE),
            (self.parts[PART_NATIONAL_SQUADS], NATIONAL_SQUAD_SIZE),
        ):
            used.update(
                v for v in struct.unpack(f"<{len(part) // 2}H", part[: len(part) // 2 * 2]) if v
            )
        return used

    def club_referenced(self) -> set[int]:
        part = self.parts[PART_CLUB_SQUADS]
        return {v for v in struct.unpack(f"<{len(part) // 2}H", part[: len(part) // 2 * 2]) if v}


@dataclass
class TeamString:
    """One string slot of an overlay's team table: where it is and how long it may be."""

    offset: int  # into the overlay entry
    capacity: int  # bytes before the next string, NUL included
    text: str


@dataclass
class OverlayTeams:
    """The team-name table of one language overlay."""

    entry: AfsEntry
    load_address: int
    table_offset: int
    clubs: dict[int, tuple[TeamString, TeamString]] = field(default_factory=dict)
    nationals: dict[int, tuple[TeamString, TeamString]] = field(default_factory=dict)


class RomReader:
    def __init__(self, iso_path: str) -> None:
        self.iso_path = iso_path
        self.size = 0
        self._extents: dict[str, FileExtent] = {}
        self._entries: dict[str, list[AfsEntry]] = {}

    # -- ISO ---------------------------------------------------------------

    def load(self) -> bool:
        """Locate the boot file and both archives; False when this is not the game."""
        if not os.path.exists(self.iso_path):
            return False
        self.size = os.path.getsize(self.iso_path)
        if self.size < MIN_ISO_SIZE:
            return False
        with open(self.iso_path, "rb") as f:
            root = iso9660.read_root(f)
            if root is None:
                return False
            for name in (BOOT_FILE, TEXT_AFS, OVER_AFS, *TEXT_LANGUAGE_AFS):
                found = iso9660.find_entry_with_next_lba(f, root, name)
                if found is None:
                    if name in TEXT_LANGUAGE_AFS:
                        continue  # a language archive may be absent on another release
                    return False
                entry, next_lba = found
                self._extents[name] = FileExtent(entry.extent.lba, entry.extent.size, next_lba)
        return True

    def has(self, name: str) -> bool:
        return name in self._extents

    def extent(self, name: str) -> FileExtent:
        if name not in self._extents:
            raise RomError(f"{name} not located; call load() first")
        return self._extents[name]

    def entries(self, afs_name: str) -> list[AfsEntry]:
        if afs_name not in self._entries:
            with open(self.iso_path, "rb") as f:
                self._entries[afs_name] = read_afs_entries(f, self.extent(afs_name).offset)
        return self._entries[afs_name]

    def entry(self, afs_name: str, index: int) -> AfsEntry:
        entries = self.entries(afs_name)
        if not 0 <= index < len(entries):
            raise RomError(f"{afs_name} has no entry {index}")
        return entries[index]

    def read(self, afs_name: str, index: int) -> bytes:
        with open(self.iso_path, "rb") as f:
            return read_entry(f, self.extent(afs_name).offset, self.entry(afs_name, index))

    def read_at(self, f: BinaryIO, afs_name: str, index: int) -> bytes:
        return read_entry(f, self.extent(afs_name).offset, self.entry(afs_name, index))

    # -- players and squads --------------------------------------------------

    def player_tables(self, index: int = PLAYER_ENTRY_LATIN) -> PlayerTables:
        header, payload = unwrap(self.read(TEXT_AFS, index))
        table = Table.parse(payload)
        needed = max(PART_PLAYERS, PART_CLUB_SQUADS, PART_CLUB_NUMBERS) + 1
        if len(table.parts) < needed:
            raise RomError(
                f"{TEXT_AFS} entry {index} has {len(table.parts)} parts, not a player table"
            )
        part_headers: list[bytes] = []
        parts: list[bytes] = []
        for block in table.parts:
            h, p = unwrap(block) if block else (b"", b"")
            part_headers.append(h)
            parts.append(p)
        if len(parts[PART_CLUB_SQUADS]) < CLUB_COUNT * CLUB_SQUAD_SIZE * 2:
            raise RomError(f"{TEXT_AFS} entry {index}: club squad table too short")
        if len(parts[PART_PLAYERS]) % PLAYER_RECORD_SIZE:
            raise RomError(f"{TEXT_AFS} entry {index}: player table is not whole records")
        return PlayerTables(header, table, part_headers, parts)

    # -- team names --------------------------------------------------------

    def overlay_teams(self, index: int = DATABASE_OVERLAY_ENGLISH) -> OverlayTeams:
        """Parse the team-name table of one `database_<lang>.ovl` overlay."""
        entry = self.entry(OVER_AFS, index)
        data = self.read(OVER_AFS, index)
        if len(data) < 64:
            raise RomError(f"{OVER_AFS} entry {index} is too small to be an overlay")
        base = struct.unpack(
            "<I", data[OVERLAY_LOAD_ADDRESS_OFFSET : OVERLAY_LOAD_ADDRESS_OFFSET + 4]
        )[0]
        table_offset = _find_team_table(data, base)
        if table_offset is None:
            raise RomError(f"{OVER_AFS} entry {index} has no team-name table")
        # Every string the table points at, so each slot's room is known.
        pointers: set[int] = set()
        rows: list[tuple[int, int, int]] = []
        off = table_offset
        while off + TEAM_ENTRY_SIZE <= len(data):
            name_ptr, code_ptr, _flags, ident = struct.unpack(
                "<IIII", data[off : off + TEAM_ENTRY_SIZE]
            )
            if not (_in_overlay(name_ptr, base, data) and _in_overlay(code_ptr, base, data)):
                break
            rows.append((name_ptr, code_ptr, ident))
            pointers.update((name_ptr, code_ptr))
            off += TEAM_ENTRY_SIZE
        ordered = sorted(pointers) + [base + table_offset]
        room = {p: q - p for p, q in zip(ordered, ordered[1:], strict=False)}
        nationals: dict[int, tuple[TeamString, TeamString]] = {}
        for k, (name_ptr, code_ptr, _ident) in enumerate(rows[:NATIONAL_NAMED]):
            nationals[k] = (
                _team_string(data, name_ptr - base, room[name_ptr]),
                _team_string(data, code_ptr - base, room[code_ptr]),
            )
        clubs: dict[int, tuple[TeamString, TeamString]] = {}
        for k, (name_ptr, code_ptr, ident) in enumerate(rows[FIRST_CLUB_ENTRY:]):
            club = ident & 0xFF
            if club != k or club >= CLUB_COUNT:
                break
            clubs[club] = (
                _team_string(data, name_ptr - base, room[name_ptr]),
                _team_string(data, code_ptr - base, room[code_ptr]),
            )
        return OverlayTeams(entry, base, table_offset, clubs, nationals)

    # -- UI textures -------------------------------------------------------

    def bundles_with(self, afs_name: str, texture: str) -> list[tuple[AfsEntry, bytes, Bundle]]:
        """Every bundle of the archive holding a texture of that name, with its
        entry and block header, in entry order."""
        from .textures import Bundle

        needle = texture.encode("ascii").ljust(16, b"\0")
        extent = self.extent(afs_name)
        found: list[tuple[AfsEntry, bytes, Bundle]] = []
        with open(self.iso_path, "rb") as f:
            for entry in self.entries(afs_name):
                if entry.size < 1024:
                    continue
                f.seek(extent.offset + entry.offset)
                block = f.read(entry.size)
                if block[0] != 0 or block[2] not in (0, 1):
                    continue
                try:
                    header, payload = unwrap(block)
                except RomError:
                    continue
                if needle not in payload:
                    continue
                try:
                    bundle = Bundle.parse(payload)
                    bundle.texture(texture)
                except (RomError, ValueError):
                    continue
                found.append((entry, header, bundle))
        return found

    def kits(self) -> KitTable:
        """The team kit records (`kits.py`): four kits per club and national team."""
        return KitTable.parse(self.read(TEXT_AFS, KIT_ENTRY))

    def texture_png(self, name: str) -> bytes:
        """The first texture of that name in the primary language archive, as PNG."""
        if not self.has(TEXT_ARCHIVE_PRIMARY):
            raise RomError(f"{TEXT_ARCHIVE_PRIMARY} is not on this disc")
        found = self.bundles_with(TEXT_ARCHIVE_PRIMARY, name)
        if not found:
            raise RomError(f"No texture named {name!r} in {TEXT_ARCHIVE_PRIMARY}")
        return found[0][2].texture(name).to_png()

    # -- summary -----------------------------------------------------------

    def get_rom_info(self) -> PESRomInfo:
        if not self.load():
            return PESRomInfo(path=self.iso_path, size=self.size, is_valid=False)
        tables = self.player_tables()
        teams = self.overlay_teams()
        clubs = [
            ClubSlot(index=club, name=name.text, code=code.text)
            for club, (name, code) in sorted(teams.clubs.items())
            if name.text
        ]
        nationals = [
            ClubSlot(index=row, name=name.text, code=code.text)
            for row, (name, code) in sorted(teams.nationals.items())
            if name.text
        ]
        return PESRomInfo(
            path=self.iso_path,
            size=self.size,
            is_valid=True,
            clubs=clubs,
            nationals=nationals,
            player_count=tables.player_count,
        )


def _in_overlay(ptr: int, base: int, data: bytes) -> bool:
    return base < ptr < base + len(data)


def _find_team_table(data: bytes, base: int) -> int | None:
    """Offset of the team table: the first run of 40+ consecutive `{name ptr,
    code ptr, u32, u32 id}` entries whose last two words are not pointers.

    The overlays also hold a shorter table of four pointers per team (alternate
    national names), which comes first in some languages; its third word is a
    pointer, which is what tells the two apart.
    """
    run_start = None
    run = 0
    for off in range(0, len(data) - TEAM_ENTRY_SIZE + 1, TEAM_ENTRY_SIZE):
        a, b, c, _d = struct.unpack("<IIII", data[off : off + TEAM_ENTRY_SIZE])
        if _in_overlay(a, base, data) and _in_overlay(b, base, data) and a < b:
            if run_start is None:
                run_start = off
            run += 1
            if run >= 40 and not _in_overlay(c, base, data):
                return run_start
        else:
            run_start = None
            run = 0
    return None


def _team_string(data: bytes, offset: int, capacity: int) -> TeamString:
    raw = data[offset : offset + capacity].split(b"\0")[0]
    return TeamString(offset, capacity, raw.decode("utf-8", "replace"))
