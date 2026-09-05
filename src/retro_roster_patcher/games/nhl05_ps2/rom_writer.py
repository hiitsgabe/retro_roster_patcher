"""Write TDB records back into an NHL 2005 (PS2) ISO.

    copy the ISO -> modify the parsed TDBs in memory -> re-RefPack each of them
    -> overwrite them inside `DB.VIV` where they already sit -> write `DB.VIV`
    back into the image at its original LBA

Patch the archive in place; never use `bigf_replace`, which reassembles the
directory and so moves every offset after the replaced file. The disc has one
allocation for `DB.VIV` and the game seeks inside it by recorded offset.
`bigf_replace_inplace` keeps every offset and zero-pads the slack, so a
recompressed table may only get *smaller*.

Two size checks follow from that, at two different layers, and both matter:

  * a recompressed `.tdb` must fit the space that `.tdb` already occupies inside
    `DB.VIV` -- `bigf_replace_inplace`'s own bound;
  * the resulting `DB.VIV` must fit the sector gap before the next file on the
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
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)
from .rom_reader import ISO_SECTOR_SIZE, NHL05PS2RomReader

# Every single-bit line-assignment flag in a ROST record, and the complete set:
# `roster_values` zeroes all of them before setting the ones a player has, so a
# flag missing from this list keeps whatever the 2004 roster left there and puts
# a retired player on the power play.
#
# Sixty-four flags, where `games/nhl07_psp` has thirty. Do not treat this as
# that list with more added. Five situations, each with two units, and only even
# strength carries a third and fourth line:
#
#   3n..  five-on-three          n = 1, 2
#   4n..  four-on-four           n = 1, 2
#   Kn..  penalty kill           n = 1, 2
#   Ln..  even strength          n = 1, 2, and 3-4 for forwards, 3 for defence
#   Pn..  power play             n = 1, 2
#
# with the position as the last two characters: `LD`, `RD`, `LW`, `RW`, `C_`.
# Then `G1__`/`G2__` for the goalies and `H`, `S` and `X` units of five, three
# and two.
#
# `33LD` and `33RD` are absent because there is no third five-on-three unit; that
# is not a hole. Here a `3n` prefix is a three-skater unit -- it carries `31C_`,
# which a defence pair cannot -- and the even-strength pairs are `L1LD`-style.
# On `games/nhl07_psp` `3n` is defence pair *n*. Upstream behaviour, known wrong,
# preserved deliberately: `stat_mapper.generate_team_line_flags` emits NHL 07's
# `3nLD`/`3nRD`, so pairs one and two land on the five-on-three units, pair three
# is dropped by `roster_values`' filter, and `L1LD` through `L3RD` stay zero on
# every patched player.
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


class NHL05PS2RomWriter:
    """Copies an ISO, edits the TDBs inside its `DB.VIV`, and writes it back.

    The instance holds the *output* image, not the input: `copy_iso` makes the
    copy and `load` opens that copy. Nothing here mutates the source ISO.
    """

    def __init__(self, iso_path: str, output_path: str) -> None:
        self.iso_path = iso_path
        self.output_path = output_path
        self.reader: NHL05PS2RomReader | None = None
        self._db_viv: bytes | None = None

    def copy_iso(self, on_progress: Callable[[float, str], None] | None = None) -> None:
        """Copy the source ISO to the output path, reporting progress.

        4 MB at a time: a PS2 disc image does not fit in a handheld's memory.

        `fsync` before returning. The next step reopens the same path `r+b` and
        seeks into it, and on the SD cards these devices boot from an unflushed
        multi-gigabyte write is a real way to read back a hole.

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
        """The output ISO's `DB.VIV` as `load` found it, before any edit.

        The edits live in the parsed `TDBFile` objects, not here, so a caller
        reading this after a write still sees the archive as it was on the disc.
        `patcher._archive_spelling` depends on that: it asks for member names,
        and the names do not change.
        """
        return self._db_viv

    def load(self) -> bool:
        """Open the copied ISO and cache its `DB.VIV`."""
        self.reader = NHL05PS2RomReader(self.output_path)
        if not self.reader.load():
            return False
        self._db_viv = self.reader.get_db_viv()
        return self._db_viv is not None

    def write_player_bio(self, tdb: TDBFile, record_idx: int, player: NHL05PlayerRecord) -> None:
        """Update one SPBT record's name, number, hand, team and position.

        A missing table, or an index past its allocation, is a no-op rather than
        an error. This game has no SPBT mirror, so that is a guard against a
        caller's bad index rather than the routine case it is on NHL 07.

        Names truncate to `NAME_FIELD_CHARS`, 15 here against NHL 07's 19:
        `FNME` and `LNME` are 16-byte fields here and 20-byte fields there.

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
        attrs: NHL05SkaterAttributes,
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
        attrs: NHL05GoalieAttributes,
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

        Set *every* one of the sixty-four flags in `LINE_FLAGS`, to zero unless
        `line_flags` names it: the record is reused, so its previous occupant's
        line is still in those bits. Drop a key that is not a known flag; that
        filter is where this game's third defence pair is silently lost, since
        the mapper emits NHL 07's `33LD`/`33RD`. See `LINE_FLAGS`.

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
        """Recompress each TDB, patch it into `DB.VIV`, write `DB.VIV` to the ISO.

        `modified_tdbs` is keyed by the archive's *own* spelling of each member,
        which the caller reads out of `bigf_parse` -- see `patcher.py`.

        Never discard `bigf_replace_inplace`'s return value: a table that does
        not fit its slot would be silently skipped, leaving `DB.VIV` with its
        two TDBs disagreeing about the same roster and the patch reported as a
        success. Raise instead.

        Raises:
            RomError: a recompressed TDB does not fit its slot inside `DB.VIV`;
                `DB.VIV` cannot be located on the ISO; or the rebuilt `DB.VIV`
                does not fit the sector gap before the next file.
        """
        if self._db_viv is None or self.reader is None:
            raise RomError("DB.VIV was never loaded; call copy_iso() and load() first")

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
                    f"the space it occupies in DB.VIV; the patched roster cannot be written"
                )

        if on_progress is not None:
            on_progress(PROGRESS_COMPRESS_END, "Writing DB.VIV to ISO...")

        # A second reader over the same output file: `load`'s cached `DB.VIV`'s
        # contents, and what is wanted here is where it sits.
        reader_for_loc = NHL05PS2RomReader(self.output_path)
        reader_for_loc.load()
        db_lba, db_orig_size, db_max_size = reader_for_loc.find_db_viv_location()
        if db_lba == 0:
            raise RomError(f"Cannot find DB.VIV inside {self.output_path}")

        new_viv_bytes = bytes(new_viv)
        if len(new_viv_bytes) > db_max_size:
            raise RomError(
                f"Rebuilt DB.VIV is {len(new_viv_bytes)} bytes and its allocation on the ISO "
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
