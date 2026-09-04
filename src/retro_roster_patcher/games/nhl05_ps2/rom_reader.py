"""Read an NHL 2005 (PS2) ISO: walk ISO 9660, pull out `DB.VIV`, parse its TDBs.

    ISO 9660 -> /DB/DB.VIV -> BIGF -> *.tdb -> tables

Mode 1 with 2048-byte sectors, which is what a PS2 DVD image is; the walk itself
is `formats/iso9660.py`, shared with `games/nhl07_psp`. **This is not the sector
layout `games/we2002` uses** -- that game reads Mode 2 / 2352 images, where each
sector carries a sync pattern, a header, a subheader and a trailing EDC/ECC
block, and the two share the word "sector" and no arithmetic.

Two differences from the NHL 07 reader, both small and both load-bearing:

  * **`DB.VIV` is one directory from the root**, not three. The path is the only
    thing `iso9660.walk` is parameterised on, so it is the whole of the
    difference in the ISO layer.
  * **`validate(deep=True)` looks for `nhl2005.tdb`**, the master, where NHL 07
    looks for its `nhlbioatt.tdb` mirror. That makes this game's heuristic
    considerably stronger -- see `validate`.
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

# Re-exported so this package's callers do not each have to know that a Mode 1
# sector is 2048 bytes; `rom_writer` and `patcher` both seek by it.
ISO_SECTOR_SIZE = iso9660.SECTOR_SIZE

# Where `DB.VIV` sits inside the image: one directory down from the root, which
# is where a PS2 disc puts it and where a PSP UMD does not. The three walks
# below all derive their path from this one constant; the source declared it and
# then spelled the directory list out again, inline, at each of the three call
# sites.
DB_VIV_PATH = "DB/DB.VIV"
DB_VIV_DIRS = tuple(DB_VIV_PATH.split("/")[:-1])
DB_VIV_NAME = DB_VIV_PATH.split("/")[-1]

# The smallest file that could hold a PVD, a root directory and the directory
# beneath it. `load` refuses anything smaller before it seeks, so a short file is
# rejected rather than read past. It is a floor and nothing more: a real PS2 DVD
# image is gigabytes, and the real bound on whether this patcher can do its job
# is `db_viv_extent_fits` in `patcher.py`.
#
# 20 sectors is the source's number and is kept. It is not derived from this
# game's shallower directory tree, which would allow a smaller floor: the point
# of the check is to refuse a file that cannot possibly be an ISO before seeking
# into it, and every byte below 40 KB is refused by both figures alike.
MIN_ISO_SIZE = ISO_SECTOR_SIZE * 20

# The first two bytes of a RefPack stream. A `DB.VIV` member may be stored
# uncompressed, so this is a test and not a requirement.
REFPACK_MAGIC = b"\x10\xfb"

# What a decompressed TDB starts with. `formats.ea_tdb` has the same constant
# and raises on a mismatch; this module tests for it instead, because
# `validate` answers a question rather than making a claim about the disc.
TDB_MAGIC = b"DB\x00\x08"

# What the archive starts with.
BIGF_MAGIC = b"BIGF"


class NHL05PS2RomReader:
    """Opens an NHL 2005 PS2 ISO and hands out its parsed TDB tables.

    Construction touches nothing. `load()` is what reads the file, and every
    other method answers from what `load` cached, so an instance is cheap to
    hold and the file handle is never kept open between calls.
    """

    def __init__(self, iso_path: str) -> None:
        self.iso_path = iso_path
        self._iso_size = 0
        self._db_viv_data: bytes | None = None
        self._tdb_files: dict[str, TDBFile] = {}

    # -- loading ------------------------------------------------------------

    def load(self) -> bool:
        """Read the ISO's directory tree and cache `DB.VIV`.

        Returns False when the file does not exist, is too small to be an ISO at
        all, or has no `/DB/DB.VIV` -- three ways of being a file that is not
        this game.

        DELIBERATE DIVERGENCE: the source wrapped the whole body in
        `except Exception: return False`, so a revoked read bit, a yanked USB
        mount and an EIO all became "not this game". `Patcher.analyze_rom`
        promises the opposite -- `RomError` for unreadable, `is_valid=False` for
        readable-but-not-mine -- and it cannot make that distinction if the
        reader has already erased it. `OSError` now propagates and the patcher
        converts it through `errors.as_rom_error`.
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

        Two depths, and they are two different strengths of claim:

        - `deep=False` asks only whether `DB.VIV` starts with `BIGF`. That is
          true of every EA disc that ships a `db.viv`, so on its own it is barely
          a signature at all.
        - `deep=True` additionally requires `nhl2005.tdb` to be present in the
          archive and to decompress to something whose first four bytes are
          `DB\\x00\\x08`.

        **The deep check is stronger here than in `games/nhl07_psp`**, and the
        difference is the file name. That game looks for `nhlbioatt.tdb`, which
        is on NHL 06, 07 and 08 discs alike, so its deep check separates "an EA
        NHL disc" from "any other EA disc" and no further. `nhl2005.tdb` names
        one year.

        Both are still **heuristics** in the sense
        `docs/.../migrate-remaining-games.md` uses: a guess about what the
        content means, never run against a real disc. `analyze_rom` uses
        `deep=True` and `patch` uses neither -- see `patcher.py` for the
        asymmetry, why it survives the deep check naming the file `patch`
        actually needs, and the test that pins it.
        """
        if not self._db_viv_data:
            return False
        if self._db_viv_data[:4] != BIGF_MAGIC:
            return False
        if not deep:
            return True
        raw = bigf_extract(self._db_viv_data, TDB_MASTER)
        if raw is None:
            # The source retried with `TDB_MASTER.lower()`, which is the same
            # string: `bigf_extract` already folds case on both sides, so the
            # retry could never find anything the first call missed.
            return False
        if raw[:2] == REFPACK_MAGIC and len(raw) > 5:
            return refpack_decompress(raw)[:4] == TDB_MAGIC
        return raw[:4] == TDB_MAGIC

    def get_info(self, deep: bool = True) -> NHL05RomInfo:
        """Validate, then describe the team slots.

        With `deep=True` the slots come from the disc's own STEA table. With
        `deep=False` they are the 30 hard-coded club names, which say nothing
        about the file -- so `analyze_rom` uses the deep path, or every NHL 2005
        ISO in a library would render identically.
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

    # -- TDB access ---------------------------------------------------------

    def get_tdb(self, filename: str) -> TDBFile | None:
        """Parse one member of `DB.VIV`, decompressing it if it is RefPacked.

        Cached by the name the caller asked for, not by the archive's own
        spelling: `bigf_extract` folds case, so `get_tdb("NHL2005.TDB")` and
        `get_tdb("nhl2005.tdb")` would each parse the file and hand out two
        independent `TDBFile` objects, and a write to one would not be seen by
        the other. Every caller in this package uses the `TDB_*` constants, so
        that never happens; the cost of it happening is why this note exists.

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

        # `len(raw) > 2` as well as the magic, so a member that is *only* the
        # two magic bytes goes to `TDBFile.parse` rather than to
        # `refpack_decompress`. Both reject it as `EaTdbError`; this is the
        # source's spelling and the message it produces names the TDB, which is
        # what the caller asked for.
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

    # -- ISO 9660 -----------------------------------------------------------

    def _db_directory(self, f: BinaryIO) -> iso9660.Extent | None:
        """The `DB` directory's extent, from the PVD down, or None.

        One helper for what the source wrote out three times, in
        `_extract_db_viv`, `find_db_viv_location` and
        `find_db_viv_dir_entry_offset`. `games/nhl07_psp` has the same helper
        over a three-name path; that difference is the whole of what
        `iso9660.walk` had to parameterise.
        """
        root = iso9660.read_root(f)
        if root is None:
            return None
        return iso9660.walk(f, root, DB_VIV_DIRS)

    def _extract_db_viv(self) -> bytes | None:
        """Walk the directory tree and read `DB.VIV`, or None if it is not there.

        None -- rather than an exception -- for every structural disappointment:
        no PVD, a missing directory, a directory where a file was expected. Each
        of those means "not this game", which `load` turns into False and the
        patcher turns into `is_valid=False`.
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
        the padding of its own last sector.

        **A PS2 disc's `/DB` directory is a real risk of that second case** in a
        way NHL 07's `/PSP_GAME/USRDIR/DB` is less of one: a directory holding
        `DB.VIV` and nothing else reports no next file, and the budget collapses
        to the archive's own length. In-place replacement keeps the length the
        same, so that is a budget the writer meets -- but it means this game has
        no headroom to grow into and a rebuild that grew by one byte is refused.

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

    # -- team slots ---------------------------------------------------------

    def _read_team_slots(self) -> list[NHL05TeamSlot]:
        """Team slots from the master TDB's STEA table, or the hard-coded names.

        **The whole of this method is different from NHL 07's**, and the source
        wrote it that way rather than by copying. Four differences:

        - It reads `FNME` and `SNME`, the full and short team names, where NHL 07
          reads `NAME` and `CITY`.
        - It reads `ABBR` out of the record and falls back to the constant, where
          NHL 07 always uses the constant.
        - It filters on `INDX > 29` rather than bounding the loop by the length
          of the name list. NHL 2005's STEA is reported to hold 94 records --
          national and historic sides beyond the 32 club slots -- and `INDX` is
          what says which of them is an NHL team.
        - It sorts by `INDX`, because with a filter rather than a bound the
          records need not arrive in slot order.

        The loop is additionally bounded by `capacity`, which the source did not
        do. That is the contract `formats/ea_tdb.py` hands to its consumers --
        it deliberately never checks `currentRecords` against `maxRecords`, so a
        file whose header overstates its own live count would otherwise raise
        `IndexError` out of `analyze_rom`. With the loop bounded there is
        nothing left for the source's per-record `try/except Exception: continue`
        to catch, and that is gone too: it was turning a bad header into a
        silently short slot list.
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
            # `read_record` answers `str` for a string field and `int` for
            # everything else, and a table without an `INDX` field at all
            # answers nothing: the record's own position stands in for it, which
            # is what the source's `rec.get("INDX", i)` meant.
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
