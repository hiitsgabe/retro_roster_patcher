"""Read an NHL 07 (PSP) ISO: walk ISO 9660, pull out `db.viv`, parse its TDBs.

    ISO 9660 -> /PSP_GAME/USRDIR/DB/DB.VIV -> BIGF -> *.tdb -> tables

Mode 1 with 2048-byte sectors, which is what a PSP UMD image is: a sector is
nothing but its 2048 payload bytes, so a logical block address multiplied by
2048 is a file offset. Do not borrow arithmetic from `games/we2002`: that game
reads Mode 2 / 2352 images, where each sector carries a 12-byte sync pattern, a
4-byte header, an 8-byte subheader and a trailing EDC/ECC block.

Nothing here reads a compressed disc image; see
`patcher.NHL07PSPPatcher.analyze_rom`.
"""

from __future__ import annotations

import os
from typing import BinaryIO

from ...formats import iso9660
from ...formats.ea_tdb import TDBFile, bigf_extract, refpack_decompress
from .models import (
    NHL07_TEAM_INDEX,
    NHL07_TEAM_NAMES,
    TDB_BIOATT,
    TDB_MASTER,
    TEAM_COUNT,
    NHL07RomInfo,
    NHL07TeamSlot,
)

ISO_SECTOR_SIZE = iso9660.SECTOR_SIZE

# Where `db.viv` sits inside the image. Derive the walk from this one constant;
# never re-spell the directory list at a call site.
DB_VIV_PATH = "PSP_GAME/USRDIR/DB/DB.VIV"
DB_VIV_DIRS = tuple(DB_VIV_PATH.split("/")[:-1])
DB_VIV_NAME = DB_VIV_PATH.split("/")[-1]

# The smallest file that could hold a PVD, a root directory and the three
# directories beneath it. A floor and nothing more; the real bound is
# `_db_viv_extent_fits` in `patcher.py`.
MIN_ISO_SIZE = ISO_SECTOR_SIZE * 20

# The first two bytes of a RefPack stream. A `db.viv` member may be stored
# uncompressed, so this is a test and not a requirement.
REFPACK_MAGIC = b"\x10\xfb"

# What a decompressed TDB starts with. `formats.ea_tdb` raises on a mismatch;
# `validate` only tests for it.
TDB_MAGIC = b"DB\x00\x08"


class NHL07PSPRomReader:
    """Opens an NHL 07 PSP ISO and hands out its parsed TDB tables.

    Construction touches nothing; `load()` reads the file and every other method
    answers from what it cached.
    """

    def __init__(self, iso_path: str) -> None:
        self.iso_path = iso_path
        self._iso_size = 0
        self._db_viv_data: bytes | None = None
        self._tdb_files: dict[str, TDBFile] = {}

    def load(self) -> bool:
        """Read the ISO's directory tree and cache `db.viv`.

        Returns False when the file does not exist, is too small to be an ISO at
        all, or has no `/PSP_GAME/USRDIR/DB/DB.VIV` -- three ways of being a file
        that is not this game.

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
        """Is this an NHL 07 PSP ISO?

        - `deep=False` asks only whether `db.viv` starts with `BIGF`, which is
          true of every EA PSP disc that ships a `db.viv`, NHL 06 and NHL 08
          included.
        - `deep=True` additionally requires `nhlbioatt.tdb` to be in the archive
          and to decompress to first bytes `DB\\x00\\x08`.

        Both are heuristics. `analyze_rom` uses `deep=True` and `patch` uses
        neither; see `patcher.py`.
        """
        if not self._db_viv_data:
            return False
        if self._db_viv_data[:4] != b"BIGF":
            return False
        if not deep:
            return True
        raw = bigf_extract(self._db_viv_data, TDB_BIOATT)
        if raw is None:
            # No case-folded retry: `bigf_extract` already folds both sides.
            return False
        if raw[:2] == REFPACK_MAGIC and len(raw) > 5:
            return refpack_decompress(raw)[:4] == TDB_MAGIC
        return raw[:4] == TDB_MAGIC

    def get_info(self, deep: bool = True) -> NHL07RomInfo:
        """Validate, then describe the team slots.

        With `deep=True` the slots come from the disc's own STEA table; with
        `deep=False` they are the hard-coded club names.
        """
        if not self._db_viv_data:
            return NHL07RomInfo(path=self.iso_path, size=0, is_valid=False)

        is_valid = self.validate(deep=deep)
        if is_valid and deep:
            team_slots = self._read_team_slots()
        elif is_valid:
            team_slots = [
                NHL07TeamSlot(
                    index=i,
                    name=NHL07_TEAM_NAMES[i],
                    abbreviation=NHL07_TEAM_INDEX.get(i, f"T{i}"),
                )
                for i in range(TEAM_COUNT)
            ]
        else:
            team_slots = []
        return NHL07RomInfo(
            path=self.iso_path,
            size=self._iso_size,
            team_slots=team_slots,
            is_valid=is_valid,
        )

    def get_tdb(self, filename: str) -> TDBFile | None:
        """Parse one member of `db.viv`, decompressing it if it is RefPacked.

        The cache is keyed by the name the caller asked for, not the archive's
        spelling, so always ask through the `TDB_*` constants: two spellings of
        one member would hand out two `TDBFile` objects and writes to one would
        be invisible to the other.

        Raises:
            EaTdbError: `db.viv` is not a BIGF, or the member is neither a
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
        """The raw `db.viv` bytes `load` cached, or None if it never loaded."""
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
        """(lba, size, max_size) for `db.viv`, or (0, 0, 0) if it is not found.

        `max_size` is the byte budget a rebuilt archive may occupy before it
        would overwrite the next file on the disc: the gap in sectors to that
        file, times 2048. With no next file in the same directory the budget is
        the archive's own sector-aligned length, so a rebuild may grow only into
        the padding of its own last sector.

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

    def _read_team_slots(self) -> list[NHL07TeamSlot]:
        """Team slots from the master TDB's STEA table, or the hard-coded names.

        All three loop bounds are load-bearing: `num_records` because a slot past
        the live count holds whatever the last roster left there, `capacity`
        because `formats/ea_tdb.py` does not check the one against the other and
        an overstated header raises `IndexError` out of `analyze_rom`, and
        `len(NHL07_TEAM_NAMES)` because every listed slot needs a fallback name.
        """
        tdb = self.get_tdb(TDB_MASTER)
        stea = tdb.get_table("STEA") if tdb else None

        if stea is None:
            return [
                NHL07TeamSlot(
                    index=i,
                    name=NHL07_TEAM_NAMES[i],
                    abbreviation=NHL07_TEAM_INDEX.get(i, f"T{i}"),
                )
                for i in range(TEAM_COUNT)
            ]

        slots: list[NHL07TeamSlot] = []
        limit = min(stea.num_records, stea.capacity, len(NHL07_TEAM_NAMES))
        for i in range(limit):
            rec = stea.read_record(i)
            name = str(rec.get("NAME", "") or rec.get("CITY", "") or "")
            # A table with no `INDX` field falls back to the record's position.
            raw_index = rec.get("INDX")
            idx = raw_index if isinstance(raw_index, int) else i
            abbr = NHL07_TEAM_INDEX.get(idx, f"T{idx}")
            if not name:
                name = NHL07_TEAM_NAMES[i] if i < len(NHL07_TEAM_NAMES) else f"Team {i}"
            slots.append(NHL07TeamSlot(index=idx, name=name, abbreviation=abbr))
        return slots
