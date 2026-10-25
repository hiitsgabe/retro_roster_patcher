"""Write TDB records back into an NHL 07 (PSP) ISO.

    copy the ISO -> modify the parsed TDBs in memory -> re-RefPack each of them
    -> overwrite them inside `db.viv` where they already sit -> write `db.viv`
    back into the image at its original LBA

When every recompressed table fits its slot, `bigf_replace_inplace` writes it
there, keeping every offset and zero-padding the slack. When one does not,
the archive is rebuilt compactly from the original with every new table
(`bigf_replace`), so a table that grew takes the room the others freed. Moving
the grown table to the end of the archive instead cost its whole size: on a
real NHL 07 disc a 706-byte growth of `nhlrost.tdb` pushed `db.viv` 22 KB past
its allocation.

The size check that remains: the resulting `db.viv` must fit the sector gap
before the next file on the ISO -- `find_db_viv_location`'s `max_size`.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable, Mapping
from typing import BinaryIO

from ...core.errors import RomError
from ...formats import iso9660
from ...formats.ea_tdb import (
    EaTdbError,
    TDBFile,
    bigf_parse,
    bigf_replace,
    bigf_replace_inplace,
    refpack_compress,
    refpack_decompress,
)
from .models import (
    NAME_FIELD_CHARS,
    POSITION_REVERSE,
    WEIGHT_BASE_POUNDS,
    NHL07GoalieAttributes,
    NHL07PlayerRecord,
    NHL07SkaterAttributes,
)
from .portraits import PORTAPT_PATH
from .rom_reader import ISO_SECTOR_SIZE, NHL07PSPRomReader

# The game's cached copy of `db.viv`'s directory: `PRELOAD/FAPT.VIV` holds, as
# `db.bh`, the first header-size bytes of `db.viv` (total size included),
# RefPack-compressed, as it does for the front-end archives.
FAPT_VIV_PATH = "PSP_GAME/USRDIR/PRELOAD/FAPT.VIV"
DB_BH_NAME = "db.bh"
# Every holder of `portapt.bh`, the cached directory of `FE/PORTAPT.VIV`.
HEADER_CACHE_HOLDERS = (FAPT_VIV_PATH, "PSP_GAME/USRDIR/PRELOAD/GMISC.VIV")
PORTAPT_BH_NAME = "portapt.bh"

# Every single-bit line-assignment flag in a ROST record, and the complete set:
# `roster_values` zeroes all of them before setting the ones a player has, so a
# flag missing from this list keeps whatever the 2006 roster left there and puts
# a retired player on the power play.
#
# The retail disc's sixty-four, in its order (ULUS10131's ROST), and the same
# list as `games/nhl05_ps2`, which explains the families:
#
#   3n.. five-on-three, 4n.. four-on-four, Kn.. penalty kill, Pn.. power play
#   Ln.. even strength: L1-L4 forward lines, L1LD/L1RD-L3LD/L3RD defence pairs
#   G1__, G2__ starting and backup goalie; H, S and X units
LINE_FLAGS = [
    "31LD",
    "41LD",
    "K1LD",
    "L1LD",
    "P1LD",
    "32LD",
    "42LD",
    "K2LD",
    "L2LD",
    "P2LD",
    "L3LD",
    "31RD",
    "41RD",
    "K1RD",
    "L1RD",
    "P1RD",
    "32RD",
    "42RD",
    "K2RD",
    "L2RD",
    "P2RD",
    "L3RD",
    "41LW",
    "K1LW",
    "L1LW",
    "P1LW",
    "42LW",
    "K2LW",
    "L2LW",
    "P2LW",
    "L3LW",
    "L4LW",
    "L1RW",
    "P1RW",
    "L2RW",
    "P2RW",
    "L3RW",
    "L4RW",
    "31C_",
    "41C_",
    "K1C_",
    "L1C_",
    "P1C_",
    "32C_",
    "42C_",
    "K2C_",
    "L2C_",
    "P2C_",
    "L3C_",
    "L4C_",
    "G1__",
    "H1__",
    "S1__",
    "X1__",
    "G2__",
    "H2__",
    "S2__",
    "X2__",
    "H3__",
    "S3__",
    "H4__",
    "S4__",
    "H5__",
    "S5__",
]

# Progress-bar spans, contiguous and monotonic, ending at 1.0:
#
#   0.00 .. 0.30   copy_iso
#   0.30 .. 0.60   patcher.patch, writing records
#   0.60 .. 1.00   rebuild_and_write, recompressing and writing back
PROGRESS_COPY_END = 0.3
PROGRESS_RECORDS_END = 0.6
PROGRESS_COMPRESS_END = 0.95


class NHL07PSPRomWriter:
    """Copies an ISO, edits the TDBs inside its `db.viv`, and writes it back.

    The instance holds the *output* image, not the input: `copy_iso` makes the
    copy and `load` opens that copy. Nothing here mutates the source ISO.
    """

    def __init__(self, iso_path: str, output_path: str) -> None:
        self.iso_path = iso_path
        self.output_path = output_path
        self.reader: NHL07PSPRomReader | None = None
        self._db_viv: bytes | None = None

    def copy_iso(self, on_progress: Callable[[float, str], None] | None = None) -> None:
        """Copy the source ISO to the output path, reporting progress.

        4 MB at a time: the whole image does not fit in a handheld's memory.

        `fsync` before returning. The next step reopens the same path `r+b` and
        seeks into it, and on the SD cards these devices boot from an unflushed
        700 MB write is a real way to read back a hole.

        Let `OSError` propagate; `patch` converts it through
        `errors.as_rom_error`, which names the file the OS complained about.
        """
        src_size = os.path.getsize(self.iso_path)
        output_dir = os.path.dirname(self.output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        chunk_size = 4 * 1024 * 1024
        copied = 0
        with open(self.iso_path, "rb") as src, open(self.output_path, "wb") as dst:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                if on_progress is not None and src_size > 0:
                    on_progress(
                        copied / src_size * PROGRESS_COPY_END,
                        f"Copying ISO... {copied // (1024 * 1024)}MB",
                    )
            dst.flush()
            os.fsync(dst.fileno())

    @property
    def db_viv(self) -> bytes | None:
        """The output ISO's `db.viv` as `load` found it, before any edit.

        The edits live in the parsed `TDBFile` objects, not here, so a caller
        reading this after a write still sees the archive as it was on the disc.
        `patcher._archive_spelling` depends on that: it asks for member names,
        and the names do not change.
        """
        return self._db_viv

    def load(self) -> bool:
        """Open the copied ISO and cache its `db.viv`."""
        self.reader = NHL07PSPRomReader(self.output_path)
        if not self.reader.load():
            return False
        self._db_viv = self.reader.get_db_viv()
        return self._db_viv is not None

    def write_player_bio(self, tdb: TDBFile, record_idx: int, player: NHL07PlayerRecord) -> None:
        """Update one SPBT record's name, number, hand, team and position.

        A missing table, or an index past its allocation, is a no-op rather than
        an error: the same player is written to the master TDB and to
        `nhlbioatt.tdb`, which need not have the same capacity.

        Write `WEIG` and `HEIG` only when positive, so a provider that reports
        no weight leaves the disc's own value alone rather than flattening the
        player to zero pounds. `WEIG` is stored as pounds above
        `WEIGHT_BASE_POUNDS`.
        """
        spbt = tdb.get_table("SPBT")
        if spbt is None or record_idx >= spbt.capacity:
            return

        values: dict[str, object] = {
            "FNME": player.first_name[:NAME_FIELD_CHARS],
            "LNME": player.last_name[:NAME_FIELD_CHARS],
            "JERS": player.jersey_number,
            "HAND": player.handedness,
            "TEAM": player.team_index,
            "POS_": POSITION_REVERSE.get(player.position, 0),
        }
        if player.weight > 0:
            values["WEIG"] = max(0, player.weight - WEIGHT_BASE_POUNDS)
        if player.height > 0:
            values["HEIG"] = player.height
        if player.skin is not None:
            values["SKIN"] = player.skin

        spbt.write_record(record_idx, values)

    def write_skater_attrs(
        self,
        tdb: TDBFile,
        record_idx: int,
        attrs: NHL07SkaterAttributes,
        player_id: int = 0,
    ) -> None:
        """Update one SPAI record from a skater's 22 ratings.

        Write `INDX` only for a positive `player_id`: the record was found *by*
        its `INDX`, and rewriting it with a wrong id detaches the attributes
        from the bio.
        """
        spai = tdb.get_table("SPAI")
        if spai is None or record_idx >= spai.capacity:
            return

        values: dict[str, object] = {
            "BALA": attrs.balance,
            "PENA": attrs.penalty,
            "SACC": attrs.shot_accuracy,
            "WACC": attrs.wrist_accuracy,
            "FACE": attrs.faceoffs,
            "ACCE": attrs.acceleration,
            "SPEE": attrs.speed,
            "POTE": attrs.potential,
            "DEKG": attrs.deking,
            "CHKG": attrs.checking,
            "TOUG": attrs.toughness,
            "FIGH": attrs.fighting,
            "PUCK": attrs.puck_control,
            "AGIL": attrs.agility,
            "HERO": attrs.hero,
            "AGGR": attrs.aggression,
            "PRES": attrs.pressure,
            "PASS": attrs.passing,
            "ENDU": attrs.endurance,
            "INJU": attrs.injury,
            "SPOW": attrs.slap_power,
            "WPOW": attrs.wrist_power,
        }
        if player_id > 0:
            values["INDX"] = player_id
        spai.write_record(record_idx, values)

    def write_goalie_attrs(
        self,
        tdb: TDBFile,
        record_idx: int,
        attrs: NHL07GoalieAttributes,
        player_id: int = 0,
    ) -> None:
        """Update one SGAI record from a goalie's 17 ratings.

        `SPEE`, `POTE`, `TOUG`, `FIGH`, `AGIL`, `PASS` and `ENDU` are spelled
        the same here as in SPAI and are different fields in a different table;
        the five save zones and `BRKA`, `REBC`, `SREC`, `POKE` and `INTE` have
        no skater counterpart.
        """
        sgai = tdb.get_table("SGAI")
        if sgai is None or record_idx >= sgai.capacity:
            return

        values: dict[str, object] = {
            "BRKA": attrs.breakaway,
            "REBC": attrs.rebound_ctrl,
            "SREC": attrs.shot_recovery,
            "SPEE": attrs.speed,
            "POKE": attrs.poke_check,
            "INTE": attrs.intensity,
            "POTE": attrs.potential,
            "TOUG": attrs.toughness,
            "FIGH": attrs.fighting,
            "AGIL": attrs.agility,
            "5HOL": attrs.five_hole,
            "PASS": attrs.passing,
            "ENDU": attrs.endurance,
            "GSH_": attrs.glove_high,
            "SSH_": attrs.stick_high,
            "GSL_": attrs.glove_low,
            "SSL_": attrs.stick_low,
        }
        if player_id > 0:
            values["INDX"] = player_id
        sgai.write_record(record_idx, values)

    @staticmethod
    def roster_values(
        jersey: int,
        dressed: int,
        line_flags: Mapping[str, int] | None = None,
    ) -> dict[str, object]:
        """The value mapping for one ROST record: jersey, dress, lines.

        Set *every* flag in `LINE_FLAGS`, to zero unless `line_flags` names it:
        the record is reused, so its previous occupant's line is still in those
        bits. Drop a key that is not a known flag.

        Leave `CAPT` alone, so the row keeps the disc's letter: nothing in a
        provider's roster says who wears it. (On the retail disc 1 is the
        captain and 2 an alternate, the reverse of what upstream wrote.)

        Do not write `TEAM` or `INDX`. The record was found by those two, and
        rewriting `INDX` breaks the ROST -> PLAY -> SPBT chain that located it.
        """
        values: dict[str, object] = {
            "JERS": jersey,
            "DRES": dressed,
        }
        for flag in LINE_FLAGS:
            values[flag] = 0
        if line_flags:
            for flag, val in line_flags.items():
                if flag in LINE_FLAGS:
                    values[flag] = val
        return values

    def rebuild_and_write(
        self,
        modified_tdbs: Mapping[str, TDBFile],
        on_progress: Callable[[float, str], None] | None = None,
    ) -> None:
        """Recompress each TDB, patch it into `db.viv`, write `db.viv` to the ISO.

        `modified_tdbs` is keyed by the archive's *own* spelling of each member,
        which the caller reads out of `bigf_parse` -- see `patcher.py`.

        Every table is written: when one no longer fits its slot the archive is
        rebuilt compactly, and no table is skipped. Skipping (what the original
        tool did) left `db.viv` with its three TDBs disagreeing about the same
        roster.

        Raises:
            RomError: a TDB is not in `db.viv`; `db.viv` cannot be located on the
                ISO; or the rebuilt `db.viv` does not fit the sector gap before the
                next file.
        """
        if self._db_viv is None or self.reader is None:
            raise RomError("db.viv was never loaded; call copy_iso() and load() first")

        total = len(modified_tdbs)
        compressed: dict[str, bytes] = {}

        for i, (tdb_name, tdb_file) in enumerate(modified_tdbs.items()):
            if on_progress is not None:
                on_progress(
                    PROGRESS_RECORDS_END
                    + (i / max(total, 1)) * (PROGRESS_COMPRESS_END - PROGRESS_RECORDS_END),
                    f"Compressing {tdb_name}...",
                )

            compressed[tdb_name] = refpack_compress(tdb_file.serialize())

        new_viv = bytearray(self._db_viv)
        if not all(bigf_replace_inplace(new_viv, n, data) for n, data in compressed.items()):
            rebuilt = bytes(self._db_viv)
            for tdb_name, data in compressed.items():
                try:
                    rebuilt = bigf_replace(rebuilt, tdb_name, data)
                except EaTdbError as e:
                    raise RomError(
                        f"db.viv holds no {tdb_name}; the patched roster cannot be written"
                    ) from e
            new_viv = bytearray(rebuilt)

        if on_progress is not None:
            on_progress(PROGRESS_COMPRESS_END, "Writing db.viv to ISO...")

        # A second reader over the same output file: `load`'s cached `db.viv`'s
        # contents, and what is wanted here is where it sits.
        reader_for_loc = NHL07PSPRomReader(self.output_path)
        reader_for_loc.load()
        db_lba, db_orig_size, db_max_size = reader_for_loc.find_db_viv_location()
        if db_lba == 0:
            raise RomError(f"Cannot find db.viv inside {self.output_path}")

        new_viv_bytes = bytes(new_viv)
        if len(new_viv_bytes) > db_max_size:
            raise RomError(
                f"Rebuilt db.viv is {len(new_viv_bytes)} bytes and its allocation on the ISO "
                f"is {db_max_size}; writing it would overwrite the next file"
            )

        with open(self.output_path, "r+b") as f:
            f.seek(db_lba * ISO_SECTOR_SIZE)
            f.write(new_viv_bytes)
            # In-place replacement keeps the archive the same length, so this
            # normally writes nothing. It is here for the case it does not:
            # trailing bytes of the previous archive left past a shorter new one
            # would still parse as part of the last file.
            remaining = db_orig_size - len(new_viv_bytes)
            if remaining > 0:
                f.write(b"\x00" * remaining)
            f.flush()
            os.fsync(f.fileno())

        new_size = len(new_viv_bytes)
        if new_size != db_orig_size:
            dir_entry_offset = reader_for_loc.find_db_viv_dir_entry_offset()
            if dir_entry_offset > 0:
                with open(self.output_path, "r+b") as f:
                    # ISO 9660 records every length twice, little-endian at +10
                    # and big-endian at +14. Writing only one of them leaves an
                    # image whose two halves disagree, which some drivers read
                    # from one field and some from the other.
                    f.seek(dir_entry_offset + 10)
                    f.write(struct.pack("<I", new_size))
                    f.seek(dir_entry_offset + 14)
                    f.write(struct.pack(">I", new_size))
                    f.flush()
                    os.fsync(f.fileno())

        self._sync_db_header_cache(self._db_viv, new_viv_bytes)

        if on_progress is not None:
            on_progress(1.0, "Complete")

    def _sync_db_header_cache(self, old_viv: bytes, new_viv: bytes) -> None:
        """Rewrite `db.bh` in `FAPT.VIV` when `db.viv`'s directory changed.

        A rebuild that moves `db.viv`'s members leaves the cached copy pointing
        at the old offsets. See `sync_header_cache`.

        Raises:
            RomError: the rewritten `FAPT.VIV` does not fit before the next file.
        """
        with open(self.output_path, "r+b") as f:
            sync_header_cache(f, FAPT_VIV_PATH, DB_BH_NAME, old_viv, new_viv)
            f.flush()
            os.fsync(f.fileno())

    def write_portraits(self, old_archive: bytes, new_archive: bytes) -> None:
        """Write a repacked `PORTAPT.VIV` and every cached copy of its directory.

        Raises:
            RomError: the archive, or a holder of its cached directory, does not
                fit before the next file.
        """
        with open(self.output_path, "r+b") as f:
            found = iso_entry(f, PORTAPT_PATH)
            if found is None:
                raise RomError(f"Cannot find {PORTAPT_PATH} inside {self.output_path}")
            write_in_place(f, *found, new_archive)
            for holder in HEADER_CACHE_HOLDERS:
                sync_header_cache(f, holder, PORTAPT_BH_NAME, old_archive, new_archive)
            f.flush()
            os.fsync(f.fileno())


def iso_entry(f: BinaryIO, path: str) -> tuple[iso9660.DirEntry, int] | None:
    """A file's directory record and the LBA of whatever starts next on the disc.

    Next anywhere on the disc, not only in the file's own directory (see
    `iso9660.next_extent_lba`); 0 when nothing follows. None when the file is
    not there.
    """
    *dirs, name = path.split("/")
    root = iso9660.read_root(f)
    if root is None:
        return None
    directory = iso9660.walk(f, root, dirs)
    entry = iso9660.find_entry(f, directory, name) if directory is not None else None
    if entry is None or entry.is_dir:
        return None
    return entry, iso9660.next_extent_lba(f, root, entry.extent.lba)


def write_in_place(f: BinaryIO, entry: iso9660.DirEntry, next_lba: int, data: bytes) -> None:
    """Overwrite a file at its LBA and correct both copies of its length.

    The room is the gap to the next file, or the file's own last sector when
    nothing follows it in its directory. A shorter file is zero-padded over
    what it replaces.

    Raises:
        RomError: `data` does not fit that room.
    """
    if next_lba > entry.extent.lba:
        room = (next_lba - entry.extent.lba) * ISO_SECTOR_SIZE
    else:
        room = -(-entry.extent.size // ISO_SECTOR_SIZE) * ISO_SECTOR_SIZE
    if len(data) > room:
        raise RomError(
            f"Rewritten {entry.name} is {len(data)} bytes and its allocation on the ISO "
            f"is {room}; writing it would overwrite the next file"
        )
    f.seek(entry.extent.offset)
    f.write(data + b"\x00" * max(0, entry.extent.size - len(data)))
    if len(data) != entry.extent.size:
        # ISO 9660 records every length twice, little-endian at +10 and
        # big-endian at +14.
        f.seek(entry.record_offset + 10)
        f.write(struct.pack("<I", len(data)))
        f.seek(entry.record_offset + 14)
        f.write(struct.pack(">I", len(data)))


def sync_header_cache(
    f: BinaryIO, holder_path: str, bh_name: str, old_archive: bytes, new_archive: bytes
) -> None:
    """Rewrite one cached copy of an archive's directory, if it is the old one.

    The PRELOAD archives hold, as `<name>.bh`, the first header-size bytes of
    several archives (their directory, total size included), RefPack-
    compressed. Nothing happens when the directory did not change, when the
    disc has no such holder or member, or when the cached copy is not the old
    directory, so a disc laid out some other way is left as it was. The copy
    is rewritten where it sits when it fits there, else the holder is rebuilt.

    Raises:
        RomError: the rewritten holder does not fit before the next file.
    """
    old_header = old_archive[: struct.unpack_from(">I", old_archive, 12)[0]]
    new_header = new_archive[: struct.unpack_from(">I", new_archive, 12)[0]]
    if old_header == new_header:
        return
    found = iso_entry(f, holder_path)
    if found is None:
        return
    entry, next_lba = found
    f.seek(entry.extent.offset)
    holder = f.read(entry.extent.size)
    try:
        member = next((e for e in bigf_parse(holder) if e.name.lower() == bh_name), None)
        if member is None:
            return
        cached = refpack_decompress(holder[member.offset : member.offset + member.size])
    except (EaTdbError, IndexError, ValueError):
        return
    if cached != old_header:
        return
    # In the cached copy's own slot when it fits, so the holder keeps its
    # layout (FMISC.VIV is 64-byte aligned, and a rebuild would align it to 128).
    data = refpack_compress(new_header)
    in_place = bytearray(holder)
    if bigf_replace_inplace(in_place, member.name, data):
        write_in_place(f, entry, next_lba, bytes(in_place))
    else:
        write_in_place(f, entry, next_lba, bigf_replace(holder, member.name, data))
