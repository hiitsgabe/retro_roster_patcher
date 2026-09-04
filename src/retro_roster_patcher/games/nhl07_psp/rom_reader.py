"""Read an NHL 07 (PSP) ISO: walk ISO 9660, pull out `db.viv`, parse its TDBs.

    ISO 9660 -> /PSP_GAME/USRDIR/DB/DB.VIV -> BIGF -> *.tdb -> tables

Mode 1 with 2048-byte sectors, which is what a PSP UMD image is: a sector is
nothing but its 2048 payload bytes, so a logical block address multiplied by
2048 is a file offset. **This is not the sector layout `games/we2002` uses.**
That game reads Mode 2 / 2352 images, where each sector carries a 12-byte sync
pattern, a 4-byte header, an 8-byte subheader and a trailing EDC/ECC block; the
two share the word "sector" and no arithmetic.

The ISO 9660 walk itself is `formats/iso9660.py`, shared with `games/nhl05_ps2`
since that migration extracted it. What stays here is the part that is about
this game: which path `db.viv` sits at, and what to do when it is not there.

Nothing in this module reads a compressed disc image. See
`patcher.NHL07PSPPatcher.analyze_rom` for what a `.cso` or `.zso` gets, and why.
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

# Re-exported so this package's callers do not each have to know that a Mode 1
# sector is 2048 bytes; `rom_writer` and `patcher` both seek by it.
ISO_SECTOR_SIZE = iso9660.SECTOR_SIZE

# Where `db.viv` sits inside the image. The three walks below all derive their
# path from this one constant; the source declared it and then spelled the
# directory list out again, inline, at each of the three call sites.
DB_VIV_PATH = "PSP_GAME/USRDIR/DB/DB.VIV"
DB_VIV_DIRS = tuple(DB_VIV_PATH.split("/")[:-1])
DB_VIV_NAME = DB_VIV_PATH.split("/")[-1]

# The smallest file that could hold a PVD, a root directory and the three
# directories beneath it. `load` refuses anything smaller before it seeks, so a
# short file is rejected rather than read past. It is a floor and nothing more:
# a real UMD image is hundreds of megabytes, and the real bound on whether this
# patcher can do its job is `db_viv_extent_fits` in `patcher.py`.
MIN_ISO_SIZE = ISO_SECTOR_SIZE * 20

# The first two bytes of a RefPack stream. A `db.viv` member may be stored
# uncompressed, so this is a test and not a requirement.
REFPACK_MAGIC = b"\x10\xfb"

# What a decompressed TDB starts with. `formats.ea_tdb` has the same constant
# and raises on a mismatch; this module tests for it instead, because
# `validate` answers a question rather than making a claim about the disc.
TDB_MAGIC = b"DB\x00\x08"


class NHL07PSPRomReader:
    """Opens an NHL 07 PSP ISO and hands out its parsed TDB tables.

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
        """Read the ISO's directory tree and cache `db.viv`.

        Returns False when the file does not exist, is too small to be an ISO at
        all, or has no `/PSP_GAME/USRDIR/DB/DB.VIV` -- three ways of being a file
        that is not this game.

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
        """Is this an NHL 07 PSP ISO?

        Two depths, and they are two different strengths of claim:

        - `deep=False` asks only whether `db.viv` starts with `BIGF`. That is
          true of every EA PSP disc that ships a `db.viv`, NHL 06 and NHL 08
          included, so on its own it is barely a signature at all.
        - `deep=True` additionally requires `nhlbioatt.tdb` to be present in the
          archive and to decompress to something whose first four bytes are
          `DB\\x00\\x08`.

        Both are **heuristics** in the sense `docs/.../migrate-remaining-games.md`
        uses: a guess about what the content means, never run against a real
        disc. `analyze_rom` uses `deep=True` and `patch` uses neither -- see
        `patcher.py` for the asymmetry and the test that pins it.

        `refpack_decompress` of a whole `nhlbioatt.tdb` is the expensive part,
        and it is paid deliberately: `deep=False` would let this patcher claim
        every EA PSP image the user owns.
        """
        if not self._db_viv_data:
            return False
        if self._db_viv_data[:4] != b"BIGF":
            return False
        if not deep:
            return True
        raw = bigf_extract(self._db_viv_data, TDB_BIOATT)
        if raw is None:
            # The source retried with `TDB_BIOATT.lower()`, which is the same
            # string: `bigf_extract` already folds case on both sides, so the
            # retry could never find anything the first call missed.
            return False
        if raw[:2] == REFPACK_MAGIC and len(raw) > 5:
            return refpack_decompress(raw)[:4] == TDB_MAGIC
        return raw[:4] == TDB_MAGIC

    def get_info(self, deep: bool = True) -> NHL07RomInfo:
        """Validate, then describe the team slots.

        With `deep=True` the slots come from the disc's own STEA table. With
        `deep=False` they are the 30 hard-coded club names, which say nothing
        about the file -- so `analyze_rom` uses the deep path, or every NHL 07
        ISO in a library would render identically.
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

    # -- TDB access ---------------------------------------------------------

    def get_tdb(self, filename: str) -> TDBFile | None:
        """Parse one member of `db.viv`, decompressing it if it is RefPacked.

        Cached by the name the caller asked for, not by the archive's own
        spelling: `bigf_extract` folds case, so `get_tdb("NHL2007.TDB")` and
        `get_tdb("nhl2007.tdb")` would each parse the file and hand out two
        independent `TDBFile` objects, and a write to one would not be seen by
        the other. Every caller in this package uses the `TDB_*` constants, so
        that never happens; the cost of it happening is why this note exists.

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
        """The raw `db.viv` bytes `load` cached, or None if it never loaded."""
        return self._db_viv_data

    # -- ISO 9660 -----------------------------------------------------------

    def _db_directory(self, f: BinaryIO) -> iso9660.Extent | None:
        """The `DB` directory's extent, from the PVD down, or None.

        One helper for what the source wrote out three times, in
        `_extract_db_viv`, `find_db_viv_location` and
        `find_db_viv_dir_entry_offset`. `games/nhl05_ps2` walks one directory
        where this walks three, and that difference -- a `Sequence[str]` -- is
        the whole of what `iso9660.walk` had to parameterise.
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

    # -- team slots ---------------------------------------------------------

    def _read_team_slots(self) -> list[NHL07TeamSlot]:
        """Team slots from the master TDB's STEA table, or the hard-coded names.

        The loop is bounded by three things and each is load-bearing:
        `num_records` because a slot past the live count holds whatever the last
        roster left there; `capacity` because `formats/ea_tdb.py` deliberately
        does not check the one against the other and a file whose header
        overstates would otherwise raise `IndexError` out of `analyze_rom`; and
        `len(NHL07_TEAM_NAMES)` because a fallback name has to exist for every
        slot listed.

        That middle bound is the contract `formats/ea_tdb.py` hands to its
        consumers -- "the bound belongs in each game's reader" -- and it is why
        the source's `try/except Exception: continue` around each record is
        gone: with the loop bounded there is nothing left for it to catch, and
        it was previously turning a bad header into thirty silently substituted
        placeholder names.
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
            # `read_record` answers `str` for a string field and `int` for
            # everything else, and a table without an `INDX` field at all
            # answers nothing: the record's own position stands in for all
            # three, which is what the source's `rec.get("INDX", i)` meant.
            raw_index = rec.get("INDX")
            idx = raw_index if isinstance(raw_index, int) else i
            abbr = NHL07_TEAM_INDEX.get(idx, f"T{idx}")
            if not name:
                name = NHL07_TEAM_NAMES[i] if i < len(NHL07_TEAM_NAMES) else f"Team {i}"
            slots.append(NHL07TeamSlot(index=idx, name=name, abbreviation=abbr))
        return slots
