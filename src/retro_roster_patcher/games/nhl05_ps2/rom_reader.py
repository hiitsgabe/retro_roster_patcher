"""Read an NHL 2005 (PS2) ISO: walk ISO 9660, pull out `DB.VIV`, parse its TDBs.

    ISO 9660 -> /DB/DB.VIV -> BIGF -> *.tdb -> tables

Mode 1 with 2048-byte sectors, which is what a PS2 DVD image is. Do not borrow
arithmetic from `games/we2002`: that game reads Mode 2 / 2352 images, where each
sector carries a sync pattern, a header, a subheader and a trailing EDC/ECC
block.

`DB.VIV` is one directory from the root, not three as on the PSP disc, and
`validate(deep=True)` looks for `nhl2005.tdb`, the master, not for a mirror.
"""

from __future__ import annotations

import os
from typing import BinaryIO

from ...formats import iso9660
from ...formats.ea_tdb import TDBFile, bigf_extract, refpack_decompress
from .models import (
    NHL05_TEAM_INDEX,
    NHL05_TEAM_NAMES,
    TDB_MASTER,
    TEAM_COUNT,
    NHL05RomInfo,
    NHL05TeamSlot,
)

ISO_SECTOR_SIZE = iso9660.SECTOR_SIZE

# Where `DB.VIV` sits inside the image: one directory down from the root, which
# is where a PS2 disc puts it and where a PSP UMD does not. Derive the walk from
# this one constant; never re-spell the directory list at a call site.
DB_VIV_PATH = "DB/DB.VIV"
DB_VIV_DIRS = tuple(DB_VIV_PATH.split("/")[:-1])
DB_VIV_NAME = DB_VIV_PATH.split("/")[-1]

# The smallest file that could hold a PVD, a root directory and the directory
# beneath it. A floor and nothing more; the real bound is `_db_viv_extent_fits`
# in `patcher.py`.
MIN_ISO_SIZE = ISO_SECTOR_SIZE * 20

# The first two bytes of a RefPack stream. A `DB.VIV` member may be stored
# uncompressed, so this is a test and not a requirement.
REFPACK_MAGIC = b"\x10\xfb"

# What a decompressed TDB starts with. `formats.ea_tdb` raises on a mismatch;
# `validate` only tests for it.
TDB_MAGIC = b"DB\x00\x08"

# What the archive starts with.
BIGF_MAGIC = b"BIGF"


class NHL05PS2RomReader:
    """Opens an NHL 2005 PS2 ISO and hands out its parsed TDB tables.

    Construction touches nothing; `load()` reads the file and every other method
    answers from what it cached.
    """

    def __init__(self, iso_path: str) -> None:
        self.iso_path = iso_path
        self._iso_size = 0
        self._db_viv_data: bytes | None = None
        self._tdb_files: dict[str, TDBFile] = {}

    def load(self) -> bool:
        """Read the ISO's directory tree and cache `DB.VIV`.

        Returns False when the file does not exist, is too small to be an ISO at
        all, or has no `/DB/DB.VIV` -- three ways of being a file that is not
        this game.

        Never catch `OSError` here: `analyze_rom` must be able to tell
        unreadable (`RomError`) from readable-but-not-mine (`is_valid=False`),
        and it converts through `errors.as_rom_error`.
        """
        if not os.path.exists(self.iso_path):
            return False
        self._iso_size = os.path.getsize(self.iso_path)
        if self._iso_size < MIN_ISO_SIZE:
            return False
        self._db_viv_data = self._extract_db_viv()
        return self._db_viv_data is not None

    def validate(self, deep: bool = True) -> bool:
        """Is this an NHL 2005 PS2 ISO?

        - `deep=False` asks only whether `DB.VIV` starts with `BIGF`, which is
          true of every EA disc that ships a `db.viv`.
        - `deep=True` additionally requires `nhl2005.tdb` to be in the archive
          and to decompress to first bytes `DB\\x00\\x08`. `nhl2005.tdb` names one
          year, so this is a stronger claim than NHL 07's `nhlbioatt.tdb` check.

        Both are heuristics. `analyze_rom` uses `deep=True` and `patch` uses
        neither; see `patcher.py`.
        """
        if not self._db_viv_data:
            return False
        if self._db_viv_data[:4] != BIGF_MAGIC:
            return False
        if not deep:
            return True
        raw = bigf_extract(self._db_viv_data, TDB_MASTER)
        if raw is None:
            # No case-folded retry: `bigf_extract` already folds both sides.
            return False
        if raw[:2] == REFPACK_MAGIC and len(raw) > 5:
            return refpack_decompress(raw)[:4] == TDB_MAGIC
        return raw[:4] == TDB_MAGIC

    def get_info(self, deep: bool = True) -> NHL05RomInfo:
        """Validate, then describe the team slots.

        With `deep=True` the slots come from the disc's own STEA table; with
        `deep=False` they are the hard-coded club names.
        """
        if not self._db_viv_data:
            return NHL05RomInfo(path=self.iso_path, size=0, is_valid=False)

        is_valid = self.validate(deep=deep)
        if is_valid and deep:
            team_slots = self._read_team_slots()
        elif is_valid:
            team_slots = [
                NHL05TeamSlot(
                    index=i,
                    name=NHL05_TEAM_NAMES[i],
                    abbreviation=NHL05_TEAM_INDEX.get(i, f"T{i}"),
                )
                for i in range(TEAM_COUNT)
            ]
        else:
            team_slots = []
        return NHL05RomInfo(
            path=self.iso_path,
            size=self._iso_size,
            team_slots=team_slots,
            is_valid=is_valid,
        )

    def get_tdb(self, filename: str) -> TDBFile | None:
        """Parse one member of `DB.VIV`, decompressing it if it is RefPacked.

        The cache is keyed by the name the caller asked for, not the archive's
        spelling, so always ask through the `TDB_*` constants: two spellings of
        one member would hand out two `TDBFile` objects and writes to one would
        be invisible to the other.

        Raises:
            EaTdbError: `DB.VIV` is not a BIGF, or the member is neither a
                RefPack stream nor a TDB.
        """
        if filename in self._tdb_files:
            return self._tdb_files[filename]

        if not self._db_viv_data:
            return None

        raw = bigf_extract(self._db_viv_data, filename)
        if raw is None:
            return None

        # `len(raw) > 2` as well as the magic, so a member that is only the two
        # magic bytes goes to `TDBFile.parse` and the `EaTdbError` names the TDB.
        if len(raw) > 2 and raw[:2] == REFPACK_MAGIC:
            decompressed = refpack_decompress(raw)
        else:
            decompressed = raw

        tdb = TDBFile.parse(decompressed)
        self._tdb_files[filename] = tdb
        return tdb

    def get_db_viv(self) -> bytes | None:
        """The raw `DB.VIV` bytes `load` cached, or None if it never loaded."""
        return self._db_viv_data

    def _db_directory(self, f: BinaryIO) -> iso9660.Extent | None:
        """The `DB` directory's extent, from the PVD down, or None."""
        root = iso9660.read_root(f)
        if root is None:
            return None
        return iso9660.walk(f, root, DB_VIV_DIRS)

    def _extract_db_viv(self) -> bytes | None:
        """Walk the directory tree and read `DB.VIV`, or None if it is not there.

        None, never an exception, for a missing PVD, a missing directory or a
        directory where a file was expected: all of those mean "not this game".
        """
        with open(self.iso_path, "rb") as f:
            db_dir = self._db_directory(f)
            if db_dir is None:
                return None

            entry = iso9660.find_entry(f, db_dir, DB_VIV_NAME)
            if entry is None or entry.is_dir:
                return None

            f.seek(entry.extent.offset)
            return f.read(entry.extent.size)

    def find_db_viv_location(self) -> tuple[int, int, int]:
        """(lba, size, max_size) for `DB.VIV`, or (0, 0, 0) if it is not found.

        `max_size` is the byte budget a rebuilt archive may occupy before it
        would overwrite the next file on the disc: the gap in sectors to that
        file, times 2048. With no next file in the same directory the budget is
        the archive's own sector-aligned length, so a rebuild may grow only into
        the padding of its own last sector. A PS2 `/DB` directory often holds
        `DB.VIV` and nothing else, so that second case is the normal one here and
        this game has no headroom: a rebuild one byte larger is refused.

        `next_lba > lba` is equivalent to `>=` here: two directory entries
        cannot share an extent, and the only other value `next_lba` takes is 0.
        """
        with open(self.iso_path, "rb") as f:
            db_dir = self._db_directory(f)
            if db_dir is None:
                return 0, 0, 0

            found = iso9660.find_entry_with_next_lba(f, db_dir, DB_VIV_NAME)
            if found is None:
                return 0, 0, 0
            entry, next_lba = found
            db_lba, db_size = entry.extent.lba, entry.extent.size
            if db_lba == 0:
                return 0, 0, 0

            if next_lba > db_lba:
                max_size = (next_lba - db_lba) * ISO_SECTOR_SIZE
            else:
                max_size = (db_size + ISO_SECTOR_SIZE - 1) // ISO_SECTOR_SIZE * ISO_SECTOR_SIZE
            return db_lba, db_size, max_size

    def find_db_viv_dir_entry_offset(self) -> int:
        """Absolute ISO offset of `DB.VIV`'s directory record, or 0.

        The writer needs it to correct the record's two length fields when the
        rebuilt archive is shorter than the original. Zero is "not found" and is
        safe as a sentinel: a directory record can never be at offset 0 of an
        image, since the first 16 sectors are the system area.
        """
        with open(self.iso_path, "rb") as f:
            db_dir = self._db_directory(f)
            if db_dir is None:
                return 0
            entry = iso9660.find_entry(f, db_dir, DB_VIV_NAME)
            return 0 if entry is None else entry.record_offset

    def _read_team_slots(self) -> list[NHL05TeamSlot]:
        """Team slots from the master TDB's STEA table, or the hard-coded names.

        Do not copy NHL 07's version over this one. This game's STEA holds
        `FNME`/`SNME` rather than `NAME`/`CITY`, carries its own `ABBR`, and is
        reported to hold 94 records -- national and historic sides beyond the 32
        club slots -- so slots are chosen by filtering `INDX` rather than by
        bounding the loop by the name list, and then sorted because a filter does
        not guarantee slot order.

        The `capacity` bound is required: `formats/ea_tdb.py` never checks
        `currentRecords` against `maxRecords`, so an overstated header would
        raise `IndexError` out of `analyze_rom`.
        """
        tdb = self.get_tdb(TDB_MASTER)
        stea = tdb.get_table("STEA") if tdb else None

        if stea is None:
            return [
                NHL05TeamSlot(
                    index=i,
                    name=NHL05_TEAM_NAMES[i],
                    abbreviation=NHL05_TEAM_INDEX.get(i, f"T{i}"),
                )
                for i in range(TEAM_COUNT)
            ]

        slots: list[NHL05TeamSlot] = []
        for i in range(min(stea.num_records, stea.capacity)):
            record = stea.read_record(i)
            # A table with no `INDX` field falls back to the record's position.
            raw_index = record.get("INDX")
            idx = raw_index if isinstance(raw_index, int) else i
            if idx > TEAM_COUNT - 1:
                continue
            name = str(record.get("FNME", "") or record.get("SNME", "") or "")
            abbr = str(record.get("ABBR", "") or "") or NHL05_TEAM_INDEX.get(idx, f"T{idx}")
            if not name:
                name = NHL05_TEAM_NAMES[idx] if idx < len(NHL05_TEAM_NAMES) else f"Team {idx}"
            slots.append(NHL05TeamSlot(index=idx, name=name, abbreviation=abbr))

        slots.sort(key=lambda slot: slot.index)
        return slots
