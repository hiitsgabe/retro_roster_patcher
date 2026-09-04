"""Write TDB records back into an NHL 07 (PSP) ISO.

    copy the ISO -> modify the parsed TDBs in memory -> re-RefPack each of them
    -> overwrite them inside `db.viv` where they already sit -> write `db.viv`
    back into the image at its original LBA

The archive is patched **in place** rather than rebuilt. `formats.ea_tdb`
offers both, and `bigf_replace` -- which reassembles the directory and so moves
every offset after the replaced file -- is not what this game can use: the disc
has one allocation for `db.viv` and everything the game seeks to inside it is
addressed by an offset the directory records. `bigf_replace_inplace` keeps every
offset and zero-pads the slack, which is why a re-compressed table is only ever
allowed to get *smaller*.

Two size checks follow from that, at two different layers, and both matter:

  * a recompressed `.tdb` must fit the space that `.tdb` already occupies inside
    `db.viv` -- `bigf_replace_inplace`'s own bound;
  * the resulting `db.viv` must fit the sector gap before the next file on the
    ISO -- `find_db_viv_location`'s `max_size`.

The source honoured the second and ignored the first; see `rebuild_and_write`.
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
# `write_roster_values` zeroes all of them before setting the ones a player has,
# so a flag missing from this list would keep whatever the 2006 roster left
# there and put a retired player on the power play.
#
# The three defence pairs are `31LD`/`31RD` through `33LD`/`33RD` and not
# `L1LD`-style, which is the naming the four forward lines use. That is the
# TDB's own spelling, not a transcription slip.
#
#   L1C_ .. L4RW   four forward lines, centre / left wing / right wing
#   31LD .. 33RD   three defence pairs, left and right
#   G1__, G2__     starting and backup goalie
#   H1__ .. H5__   the power-play unit
#   S1__ .. S5__   the penalty-kill unit
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

# How much of a progress bar each phase of a patch owns, as fractions of the
# whole. Copying the image is by far the slowest step on real hardware -- a UMD
# image is hundreds of megabytes -- and recompressing three TDBs is the next.
#
#   0.00 .. 0.30   copy_iso
#   0.30 .. 0.60   patcher.patch, writing records
#   0.60 .. 1.00   rebuild_and_write, recompressing and writing back
#
# IMPROVEMENT: the source's three spans were 0.0-0.3, 0.35-0.60 and *0.3*-0.7,
# so a progress bar ran forwards to 60%, jumped back to 30%, and finished at
# 70% having reported "Complete". These three are contiguous and monotonic and
# the last one ends at 1.0.
PROGRESS_COPY_END = 0.3
PROGRESS_RECORDS_END = 0.6
PROGRESS_COMPRESS_END = 0.95


class NHL07PSPRomWriter:
    """Copies an ISO, edits the TDBs inside its `db.viv`, and writes it back.

    The instance holds the *output* image, not the input: `copy_iso` makes the
    copy and `load` then opens that copy, so every subsequent read and every
    write is against the file the user will keep. Nothing here mutates the
    source ISO.
    """

    def __init__(self, iso_path: str, output_path: str) -> None:
        self.iso_path = iso_path
        self.output_path = output_path
        self.reader: NHL07PSPRomReader | None = None
        self._db_viv: bytes | None = None

    def copy_iso(self, on_progress: Callable[[float, str], None] | None = None) -> None:
        """Copy the source ISO to the output path, reporting progress.

        4 MB at a time, because the whole image does not fit in a handheld's
        memory: the two applications this library was extracted for run on
        Batocera handhelds and on Android.

        `fsync` before returning, deliberately. The next step reopens the same
        path `r+b` and seeks into it, and on the SD cards these devices boot from
        an unflushed 700 MB write is a real way to read back a hole.

        DELIBERATE DIVERGENCE: the source caught every exception and returned
        `False`, which its caller turned into the message "Failed to copy ISO
        file" with the actual `errno` discarded -- a full card, a read-only
        mount and a vanished source all read the same. `OSError` now propagates
        and `patch` converts it through `errors.as_rom_error`, which names the
        file the OS complained about.
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

        The edits live in the parsed `TDBFile` objects, not here:
        `rebuild_and_write` copies these bytes and patches the recompressed
        tables into the copy. So a caller reading this after a write still sees
        the archive exactly as it was on the disc, which is what
        `patcher._archive_spelling` wants -- it is asking for names, and the
        names do not change.
        """
        return self._db_viv

    def load(self) -> bool:
        """Open the copied ISO and cache its `db.viv`.

        False when the copy has no `db.viv`, which after a successful `copy_iso`
        of a validated source means the copy is not what was copied.
        """
        self.reader = NHL07PSPRomReader(self.output_path)
        if not self.reader.load():
            return False
        self._db_viv = self.reader.get_db_viv()
        return self._db_viv is not None

    # -- record writes ------------------------------------------------------

    def write_player_bio(self, tdb: TDBFile, record_idx: int, player: NHL07PlayerRecord) -> None:
        """Update one SPBT record's name, number, hand, team and position.

        A table this TDB does not have, or an index past its allocation, is a
        no-op rather than an error: the same player is written to the master TDB
        and to `nhlbioatt.tdb`, and the second of those need not have the same
        capacity as the first.

        `WEIG` is written only when it is positive, so a provider that reports
        no weight leaves the disc's own value alone rather than flattening the
        player to zero pounds.

        DELIBERATE DIVERGENCE -- `HEIG` is not written at all. The source wrote
        it from `getattr(player, "height", 0)` against a `Player` that has no
        `height` attribute in either the old models or this library's, so the
        expression was `0` for every player who ever passed through it, the
        `if player_height > 0` branch never ran, and `NHL07PlayerRecord.height`
        kept its default of 16. The effect was that **every patched player was
        written at the same 5'10"**, overwriting whatever the disc knew. Not
        writing the field preserves the disc's per-player heights, which is
        strictly more information than one constant. Restoring the write means
        first giving `sports.models.Player` a height the providers actually
        supply; until then there is nothing to write.
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

        spbt.write_record(record_idx, values)

    def write_skater_attrs(
        self,
        tdb: TDBFile,
        record_idx: int,
        attrs: NHL07SkaterAttributes,
        player_id: int = 0,
    ) -> None:
        """Update one SPAI record from a skater's 22 ratings.

        `INDX` is written only for a positive `player_id`. Zero means "leave the
        record's identity alone", which is what every call in this package
        wants: the record was found *by* its `INDX`, so rewriting it with the
        same value is at best a no-op and at worst -- if the caller passed the
        wrong id -- detaches the attributes from the bio.
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

        **Every** flag in `LINE_FLAGS` is set, to zero unless `line_flags` names
        it, because a record is being reused rather than created: whatever line
        its previous occupant played on is still in those bits.

        A key in `line_flags` that is not a known flag is dropped rather than
        passed through. `TDBTable.write_record` would ignore it anyway -- an
        unknown field name is silently skipped there -- so this is a second
        guard on the same thing, and it is the one that keeps the value mapping
        honest for a caller that inspects it.

        Deliberately does NOT write `TEAM` or `INDX`. The record was found by
        those two, and rewriting `INDX` would break the ROST -> PLAY -> SPBT
        chain that located it.
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

    # -- writing back -------------------------------------------------------

    def rebuild_and_write(
        self,
        modified_tdbs: Mapping[str, TDBFile],
        on_progress: Callable[[float, str], None] | None = None,
    ) -> None:
        """Recompress each TDB, patch it into `db.viv`, write `db.viv` to the ISO.

        `modified_tdbs` is keyed by the archive's *own* spelling of each member,
        which the caller reads out of `bigf_parse` -- see `patcher.py`.

        DELIBERATE DIVERGENCE, twice over:

        1. **`bigf_replace_inplace`'s return value is checked.** The source
           discarded it, under a comment reasoning that a `.tdb` too large for
           its slot could be skipped because "the master TDB has all tables so
           split TDBs can stay unchanged". Nothing in this repository can check
           that claim, and it is a claim about which file the game reads at run
           time. What is certain is the failure mode it produced: a table's
           edits silently dropped, `db.viv` written back with two of its three
           TDBs disagreeing about the same roster, and `PatchResult` still
           returned as a success. That is the "success with zero work" report
           this project has now found in four patchers. It raises.
        2. **Failures raise `RomError` rather than returning `False` with the
           message on `self._last_error`.** The source stashed a message and a
           formatted traceback on the instance and its caller fished them out
           with `getattr(..., "unknown")`, so a failure whose cause was not one
           of the two anticipated ones reported the string `unknown`.

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

        # A second reader over the same output file. The one `load` built has
        # already cached `db.viv`'s *contents*; what is wanted here is where it
        # sits, which nothing cached.
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
