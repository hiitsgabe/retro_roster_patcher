"""Write TDB records back into an NHL 2005 (PS2) ISO.

    copy the ISO -> modify the parsed TDBs in memory -> re-RefPack each of them
    -> overwrite them inside `DB.VIV` where they already sit -> write `DB.VIV`
    back into the image at its original LBA

The archive is patched **in place** rather than rebuilt. `formats.ea_tdb`
offers both, and `bigf_replace` -- which reassembles the directory and so moves
every offset after the replaced file -- is not what this game can use: the disc
has one allocation for `DB.VIV` and everything the game seeks to inside it is
addressed by an offset the directory records. `bigf_replace_inplace` keeps every
offset and zero-pads the slack, which is why a re-compressed table is only ever
allowed to get *smaller*.

Two size checks follow from that, at two different layers, and both matter:

  * a recompressed `.tdb` must fit the space that `.tdb` already occupies inside
    `DB.VIV` -- `bigf_replace_inplace`'s own bound;
  * the resulting `DB.VIV` must fit the sector gap before the next file on the
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
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)
from .rom_reader import ISO_SECTOR_SIZE, NHL05PS2RomReader

# Every single-bit line-assignment flag in a ROST record, and the complete set:
# `roster_values` zeroes all of them before setting the ones a player has, so a
# flag missing from this list would keep whatever the 2004 roster left there and
# put a retired player on the power play.
#
# **Sixty-four flags, where `games/nhl07_psp` has thirty, and this is not that
# list with more added.** NHL 2005's ROST names five situations, each with two
# units, and only the even-strength one carries a third and fourth line:
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
# **`33LD` and `33RD` are absent, and that is a real inherited defect.**
# `stat_mapper.generate_team_line_flags` emits `31LD`, `31RD`, `32LD`, `32RD`,
# `33LD` and `33RD` for the three defence pairs -- NHL 07's spelling -- and
# `roster_values` drops a key this list does not name. So the third defence pair
# is never assigned on this game. It is **not fixed here**, deliberately, and
# `tests/games/nhl05_ps2/test_rom_writer.py` pins the behaviour so the decision
# is visible rather than forgotten:
#
#   * Adding `33LD`/`33RD` would write nothing. `TDBTable.write_record` skips a
#     field name the table does not have, and this list is the evidence that
#     NHL 2005's ROST does not have them.
#   * Redirecting the third pair to `L3LD`/`L3RD`, which this game does have, is
#     the fix that would do something -- and it is only right if `L1LD`/`L2LD`
#     are the first two even-strength pairs, in which case the mapper's
#     `31LD`/`32LD` are writing the first two pairs to the five-on-three unit
#     and the defect is four flags wide rather than two.
#
# Choosing between those needs a fact about the disc that this repository cannot
# establish: no real ISO may enter it, and the list below has never been checked
# against one. Guessing wrong moves real players onto the wrong special-teams
# unit, which is worse than dropping two flag bits. Recorded as a follow-up.
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

# How much of a progress bar each phase of a patch owns, as fractions of the
# whole. Copying the image is by far the slowest step on real hardware -- a PS2
# DVD image is gigabytes -- and recompressing two TDBs is the next.
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


class NHL05PS2RomWriter:
    """Copies an ISO, edits the TDBs inside its `DB.VIV`, and writes it back.

    The instance holds the *output* image, not the input: `copy_iso` makes the
    copy and `load` then opens that copy, so every subsequent read and every
    write is against the file the user will keep. Nothing here mutates the
    source ISO.
    """

    def __init__(self, iso_path: str, output_path: str) -> None:
        self.iso_path = iso_path
        self.output_path = output_path
        self.reader: NHL05PS2RomReader | None = None
        self._db_viv: bytes | None = None

    def copy_iso(self, on_progress: Callable[[float, str], None] | None = None) -> None:
        """Copy the source ISO to the output path, reporting progress.

        4 MB at a time, because the whole image does not fit in a handheld's
        memory: the two applications this library was extracted for run on
        Batocera handhelds and on Android, and a PS2 disc image is the largest
        thing either of them will be asked to copy.

        `fsync` before returning, deliberately. The next step reopens the same
        path `r+b` and seeks into it, and on the SD cards these devices boot from
        an unflushed multi-gigabyte write is a real way to read back a hole.

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
        """The output ISO's `DB.VIV` as `load` found it, before any edit.

        The edits live in the parsed `TDBFile` objects, not here:
        `rebuild_and_write` copies these bytes and patches the recompressed
        tables into the copy. So a caller reading this after a write still sees
        the archive exactly as it was on the disc, which is what
        `patcher._archive_spelling` wants -- it is asking for names, and the
        names do not change.
        """
        return self._db_viv

    def load(self) -> bool:
        """Open the copied ISO and cache its `DB.VIV`.

        False when the copy has no `DB.VIV`, which after a successful `copy_iso`
        of a validated source means the copy is not what was copied.
        """
        self.reader = NHL05PS2RomReader(self.output_path)
        if not self.reader.load():
            return False
        self._db_viv = self.reader.get_db_viv()
        return self._db_viv is not None

    # -- record writes ------------------------------------------------------

    def write_player_bio(self, tdb: TDBFile, record_idx: int, player: NHL05PlayerRecord) -> None:
        """Update one SPBT record's name, number, hand, team and position.

        A table this TDB does not have, or an index past its allocation, is a
        no-op rather than an error. NHL 07 needs that because it writes the same
        player to a master and a mirror of different capacities; this game has no
        mirror for SPBT, so here it is a guard against a caller's bad index
        rather than a routine occurrence.

        Names are truncated to `NAME_FIELD_CHARS`, which is 15 for this game
        against NHL 07's 19 -- `FNME` and `LNME` are 16-byte fields here and
        20-byte fields there. `models.NAME_FIELD_BYTES` derives it.

        `WEIG` is written only when it is positive, so a provider that reports
        no weight leaves the disc's own value alone rather than flattening the
        player to zero pounds.

        DELIBERATE DIVERGENCE -- `HEIG` is not written at all. The source wrote
        it from `getattr(player, "height", 0)` against a `Player` that has no
        `height` attribute in either the old models or this library's, so the
        expression was `0` for every player who ever passed through it, the
        `if player_height > 0` branch never ran, and `NHL05PlayerRecord.height`
        kept its default of 16. The effect was that **every patched player was
        written at the same 5'10"**, overwriting whatever the disc knew. Not
        writing the field preserves the disc's per-player heights, which is
        strictly more information than one constant. Restoring the write means
        first giving `sports.models.Player` a height the providers actually
        supply; until then there is nothing to write. `games/nhl07_psp` made the
        same call for the same reason.
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
        attrs: NHL05SkaterAttributes,
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

        **Every** flag in `LINE_FLAGS` is set, to zero unless `line_flags` names
        it, because a record is being reused rather than created: whatever line
        its previous occupant played on is still in those bits. Sixty-four of
        them here.

        A key in `line_flags` that is not a known flag is dropped rather than
        passed through. `TDBTable.write_record` would ignore it anyway -- an
        unknown field name is silently skipped there -- so this is a second
        guard on the same thing, and it is the one that keeps the value mapping
        honest for a caller that inspects it. **It is also where this game's
        third defence pair is lost**, since the mapper emits `33LD`/`33RD` and
        `LINE_FLAGS` does not name them; the comment on that list argues why
        that is preserved rather than fixed.

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
        """Recompress each TDB, patch it into `DB.VIV`, write `DB.VIV` to the ISO.

        `modified_tdbs` is keyed by the archive's *own* spelling of each member,
        which the caller reads out of `bigf_parse` -- see `patcher.py`.

        DELIBERATE DIVERGENCE, twice over:

        1. **`bigf_replace_inplace`'s return value is checked.** The source
           discarded it, under a comment reasoning that a `.tdb` too large for
           its slot could be skipped because "the master TDB has all tables so
           split TDBs can stay unchanged". Nothing in this repository can check
           that claim, and it is a claim about which file the game reads at run
           time. What is certain is the failure mode it produced: a table's
           edits silently dropped, `DB.VIV` written back with its two TDBs
           disagreeing about the same roster, and `PatchResult` still returned as
           a success. That is the "success with zero work" report this project
           has now found in four patchers. It raises.
        2. **Failures raise `RomError` rather than returning `False` with the
           message on `self._last_error`.** The source stashed a message and a
           formatted traceback on the instance and its caller fished them out
           with `getattr(..., "unknown")`, so a failure whose cause was not one
           of the two anticipated ones reported the string `unknown`.

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

        # A second reader over the same output file. The one `load` built has
        # already cached `DB.VIV`'s *contents*; what is wanted here is where it
        # sits, which nothing cached.
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
