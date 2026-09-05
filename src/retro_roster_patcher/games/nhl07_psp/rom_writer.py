"""Write TDB records back into an NHL 07 (PSP) ISO.

    copy the ISO -> modify the parsed TDBs in memory -> re-RefPack each of them
    -> overwrite them inside `db.viv` where they already sit -> write `db.viv`
    back into the image at its original LBA

Patch the archive in place; never use `bigf_replace`, which reassembles the
directory and so moves every offset after the replaced file. The disc has one
allocation for `db.viv` and the game seeks inside it by recorded offset.
`bigf_replace_inplace` keeps every offset and zero-pads the slack, so a
recompressed table may only get *smaller*.

Two size checks follow from that, at two different layers, and both matter:

  * a recompressed `.tdb` must fit the space that `.tdb` already occupies inside
    `db.viv` -- `bigf_replace_inplace`'s own bound;
  * the resulting `db.viv` must fit the sector gap before the next file on the
    ISO -- `find_db_viv_location`'s `max_size`.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable, Mapping

from ...core.errors import RomError
from ...formats.ea_tdb import TDBFile, bigf_replace_inplace, refpack_compress
from .models import (
    NAME_FIELD_CHARS,
    POSITION_REVERSE,
    NHL07GoalieAttributes,
    NHL07PlayerRecord,
    NHL07SkaterAttributes,
)
from .rom_reader import ISO_SECTOR_SIZE, NHL07PSPRomReader

# Every single-bit line-assignment flag in a ROST record, and the complete set:
# `roster_values` zeroes all of them before setting the ones a player has, so a
# flag missing from this list keeps whatever the 2006 roster left there and puts
# a retired player on the power play.
#
#   L1C_ .. L4RW   four forward lines, centre / left wing / right wing
#   31LD .. 33RD   three defence pairs, left and right
#   G1__, G2__     starting and backup goalie
#   H1__ .. H5__   the power-play unit
#   S1__ .. S5__   the penalty-kill unit
#
# `31LD` and not `L1LD`: on this game a `3n` prefix is defence pair *n*, and the
# list carries no other defence flag. On `games/nhl05_ps2` the same `3n` prefix
# is a three-skater strength unit -- it has `31C_`, which a pair cannot -- and
# the pairs there are `L1LD`-style. Do not harmonise the two lists.
LINE_FLAGS = [
    "L1C_",
    "L2C_",
    "L3C_",
    "L4C_",
    "L1LW",
    "L2LW",
    "L3LW",
    "L4LW",
    "L1RW",
    "L2RW",
    "L3RW",
    "L4RW",
    "31LD",
    "32LD",
    "33LD",
    "31RD",
    "32RD",
    "33RD",
    "G1__",
    "G2__",
    "H1__",
    "H2__",
    "H3__",
    "H4__",
    "H5__",
    "S1__",
    "S2__",
    "S3__",
    "S4__",
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
        player to zero pounds.

        Upstream behaviour, known wrong, preserved deliberately: `HEIG` is
        written and it is always 16, about 5'10", because
        `stat_mapper.map_player` reads a `Player.height` that does not exist.
        Every patched player overwrites the disc's per-player height with that
        one value.
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
            values["WEIG"] = player.weight
        if player.height > 0:
            values["HEIG"] = player.height

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
        captain: int,
        dressed: int,
        line_flags: Mapping[str, int] | None = None,
    ) -> dict[str, object]:
        """The value mapping for one ROST record: jersey, captaincy, lines.

        Set *every* flag in `LINE_FLAGS`, to zero unless `line_flags` names it:
        the record is reused, so its previous occupant's line is still in those
        bits. Drop a key that is not a known flag.

        Do not write `TEAM` or `INDX`. The record was found by those two, and
        rewriting `INDX` breaks the ROST -> PLAY -> SPBT chain that located it.
        """
        values: dict[str, object] = {
            "JERS": jersey,
            "CAPT": captain,
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

        Never discard `bigf_replace_inplace`'s return value: a table that does
        not fit its slot would be silently skipped, leaving `db.viv` with its
        three TDBs disagreeing about the same roster and the patch reported as a
        success. Raise instead.

        Raises:
            RomError: a recompressed TDB does not fit its slot inside `db.viv`;
                `db.viv` cannot be located on the ISO; or the rebuilt `db.viv`
                does not fit the sector gap before the next file.
        """
        if self._db_viv is None or self.reader is None:
            raise RomError("db.viv was never loaded; call copy_iso() and load() first")

        new_viv = bytearray(self._db_viv)
        total = len(modified_tdbs)

        for i, (tdb_name, tdb_file) in enumerate(modified_tdbs.items()):
            if on_progress is not None:
                on_progress(
                    PROGRESS_RECORDS_END
                    + (i / max(total, 1)) * (PROGRESS_COMPRESS_END - PROGRESS_RECORDS_END),
                    f"Compressing {tdb_name}...",
                )

            compressed = refpack_compress(tdb_file.serialize())
            if not bigf_replace_inplace(new_viv, tdb_name, compressed):
                raise RomError(
                    f"Recompressed {tdb_name} is {len(compressed)} bytes and does not fit "
                    f"the space it occupies in db.viv; the patched roster cannot be written"
                )

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

        if on_progress is not None:
            on_progress(1.0, "Complete")
