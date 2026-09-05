"""Read `database.big` out of an MVP Baseball (PSP) ISO and parse its CSV tables.

    ISO -> seek(DATABASE_BIG_LBA * 2048) -> 19 RefPack streams -> 19 CSV tables

**No ISO 9660 walk.** `models.DATABASE_BIG_LBA` says where the file is and the
reader seeks there. `formats/iso9660.py` exists and is used by the two NHL disc
games; this one does not need it and does not import it. See `models` for why
that is deliberate rather than an omission.

Three things about the parse are worth knowing before reading the code.

**A record's id is not its position.** Records are keyed by a nine-hex-digit id
and `record_order` keeps the sequence separately, because rewriting a table has
to preserve the order the disc had -- the game's own indices into these tables
are positional even though the links between tables are not.

**The header line is skipped by shape, not by position.** A section's first line
names its columns; every later line starts with a hex id. `_looks_like_record_id`
is the test, and it is why a column name containing a space or a non-hex
character cannot be mistaken for a record.

**Values are not stripped.** `"0 "` is field 0 holding the empty string, which
is different from field 0 being absent, and the split is on the *first* space
only so a value containing spaces survives.
"""

from __future__ import annotations

import os

from ...formats.ea_tdb import refpack_decompress
from .models import (
    ATTRIB_FIRST_NAME,
    ATTRIB_LAST_NAME,
    ATTRIB_SECTION_OFFSET,
    DATABASE_BIG_SIZE,
    HASH_ID_CHARS,
    MVP_TEAM_ABBREVS,
    MVP_TEAM_ORDER,
    ROSTER_PLAYERID,
    ROSTER_TEAMID,
    SECTION_MAP,
    TEAM_COUNT,
    TEAM_HASHES,
    MVPRomInfo,
    MVPTeamSlot,
    database_big_extent,
)

# What every section but the first begins with: the RefPack magic, big-endian
# flags 0x10 then 0xFB.
REFPACK_MAGIC = b"\x10\xfb"

# What section 0 begins with instead. 0xC0 sets the "decompressed size is three
# bytes and there is a compressed size too" flag, which `refpack_decompress`
# does not implement; `decompress_section` rewrites the two-byte header and
# leaves the rest of the stream alone. That works because the extra header word
# 0xC0 would introduce sits in bytes 2-4, which the rewrite drops -- so the
# fixup is only correct if the real header is five bytes either way. Carried
# over from the source unverified; no disc may enter this repository to check
# it.
COMPACT_SECTION_FLAG = 0xC0

# The two byte values `validate` accepts at offset 0.
VALID_FIRST_BYTES = (REFPACK_MAGIC[0], COMPACT_SECTION_FLAG)

# The digits a record id may contain. Lower case only, which is what the disc
# uses and what `TEAM_HASHES` is written in; an upper-case id would be rejected
# as a header line.
HASH_ID_DIGITS = frozenset("0123456789abcdef")

# What terminates a record. The trailing comma before the semicolon is part of
# the record, not of the terminator.
RECORD_TERMINATOR = ";\r\n"

# A parsed table: `{record id: {column number: value}}`.
Table = dict[str, dict[int, str]]


def _looks_like_record_id(text: str) -> bool:
    """Is `text` a record id rather than the first column of a header line?

    Nine lower-case hex digits is what the disc uses, but the source's test was
    "at least five characters, all of them hex, no spaces" and that is kept: a
    shorter id is not something this repository can rule out, and rejecting one
    would silently drop a whole record.
    """
    if len(text) < 5:
        return False
    return all(c in HASH_ID_DIGITS for c in text)


def _parse_record_body(parts: list[str]) -> dict[int, str]:
    """Turn `["0 Ichiro", "1 Suzuki", "22 61", ""]` into `{0: ..., 1: ..., 22: ...}`.

    Each part is a column number, one space, then the value. The split is on the
    first space so a value may contain spaces; the value is *not* stripped, so
    `"0 "` records column 0 as the empty string rather than dropping it.

    A part that is blank after stripping is skipped -- that is the empty string
    the trailing `,;` leaves -- and so is a part with no space in it at all, and
    one whose column number is not an integer.
    """
    fields: dict[int, str] = {}
    for part in parts:
        if not part.strip():
            continue
        space_idx = part.find(" ")
        if space_idx < 0:
            continue
        try:
            field_num = int(part[:space_idx])
        except ValueError:
            continue
        fields[field_num] = part[space_idx + 1 :]
    return fields


class MVPPSPRomReader:
    """Opens an MVP Baseball PSP ISO and hands out its parsed CSV tables.

    Construction touches nothing. `load()` reads the 386 977 bytes of
    `database.big` into memory and every other method answers from that, so the
    file handle is never held between calls.

    Seven accessors from the source are gone -- `get_team_roster`,
    `get_player_attribs`, `get_player_lr_attribs`, `get_pitch_attribs`,
    `get_existing_player_hashes`, `get_existing_team_hashes` and the writer's
    `remove_player_record`. None had a caller anywhere in the source package or
    the application above it, and each was a second spelling of an item lookup
    in `records`. Dropping them is a labelled change; see the package docstring.
    """

    def __init__(self, iso_path: str) -> None:
        self.iso_path = iso_path
        self.database_big: bytes | None = None
        self.database_big_offset = 0
        # table name -> decompressed CSV bytes
        self.sections: dict[str, bytes] = {}
        # table name -> {record id: {column: value}}
        self.records: dict[str, Table] = {}
        # table name -> record ids in the order the disc holds them
        self.record_order: dict[str, list[str]] = {}

    # -- loading ------------------------------------------------------------

    def load(self) -> bool:
        """Read `database.big` out of the image.

        False, without raising, for a file that does not exist, cannot be read,
        or is shorter than the extent. The last of those is the same
        condition `patcher._database_big_extent_fits` checks explicitly, and the
        duplication is deliberate: this method answers one question with one
        boolean for four different reasons, so the patcher needs a separate
        check to tell a user *which* reason applies.

        A short read is refused rather than accepted, and that closes the
        silent-corruption path before it starts: `refpack_decompress` returns
        short for a truncated stream and never pads
        (`formats/ea_tdb.py`, inherited contract 3), so a partial
        `database.big` would rebuild every later section from a truncated table
        and report success.
        """
        if not os.path.exists(self.iso_path):
            return False
        offset, _ = database_big_extent()
        try:
            with open(self.iso_path, "rb") as f:
                f.seek(offset)
                data = f.read(DATABASE_BIG_SIZE)
        except OSError:
            # DELIBERATE DIVERGENCE: `except Exception` upstream, one of six in
            # the package. Narrowed to `OSError`, which is what a seek or a read
            # can raise; anything else here is a bug in this module and must not
            # be reported to the user as "not this game".
            return False
        if len(data) < DATABASE_BIG_SIZE:
            return False
        self.database_big = data
        self.database_big_offset = offset
        return True

    # -- validation ---------------------------------------------------------

    def validate(self) -> bool:
        """Does the loaded blob look like this game's `database.big`?

        Three bytes: offset 0 is 0x10 or 0xC0, and offsets 324-325 are 0x10 0xFB.
        That is the source's check and it is kept as the cheap gate, but it is
        **not** what `analyze_rom` decides on by itself -- see `validate_deep`.

        The source guarded these reads with `len(...) < 5` and `> 326` tests
        that `load` makes unreachable: `load` refuses anything that did not
        yield all 386 977 bytes. The guards are gone and the precondition is
        stated instead.
        """
        data = self.database_big
        if data is None:
            return False
        if data[0] not in VALID_FIRST_BYTES:
            return False
        return data[ATTRIB_SECTION_OFFSET : ATTRIB_SECTION_OFFSET + 2] == REFPACK_MAGIC

    def validate_deep(self) -> bool:
        """`validate`, and then: does the `team` table hold MVP Baseball's teams?

        **This is a heuristic and it guards `analyze_rom` only.** What makes it
        one is that it is a claim about meaning -- "a table whose record ids
        include `00b87d5f5` is MVP Baseball's team table" -- rather than
        arithmetic about sizes. A false positive would make this patcher claim
        an unrelated 700 MB image, which costs a user every ISO they own; a
        false negative costs only auto-detection, because `patch --game mvp-psp`
        routes around `analyze_rom` entirely.

        Why the shallow check is not enough on its own. It is three bytes at
        three fixed offsets inside a file that must already be at least 686 MB
        long: a byte in `{0x10, 0xC0}`, then 0x10, then 0xFB. Against arbitrary
        content that is about one image in eight million, which sounds
        sufficient and is not the population that matters. The population that
        matters is *other EA PSP discs of the same era built by the same
        tooling*, where a RefPack stream at a sector boundary is not a
        coincidence at all -- it is the house format. Those three bytes
        distinguish this game from noise and do nothing to distinguish it from
        its siblings. The team ids do: they are this database's own primary
        keys.

        One match is enough, not thirty. A disc modified by a previous patcher,
        or a regional variant that dropped a club, should still be recognised;
        thirty-of-thirty would turn a heuristic into an equality test on data
        this repository cannot verify.
        """
        if not self.validate():
            return False
        if not self.sections:
            self.decompress_all()
        if not self.records:
            self.parse_all()
        team_ids = self.records.get("team", {})
        return any(team_hash in team_ids for team_hash in TEAM_HASHES.values())

    # -- decompression ------------------------------------------------------

    def decompress_section(self, offset: int) -> bytes | None:
        """Decompress the RefPack stream that starts at `offset`.

        None when nothing is loaded, when the offset is past the end, or when
        the bytes there are not a RefPack header this reader knows how to read.

        Section 0 is the special case: its flag byte is 0xC0 where every other
        section's is 0x10, and `refpack_decompress` only accepts 0x10 0xFB. The
        two bytes are rewritten and the rest of the stream is passed through
        unchanged.
        """
        data = self.database_big
        if data is None:
            return None
        if offset >= len(data):
            return None

        raw = data[offset:]
        if len(raw) < 2:
            return None
        if offset == 0 and raw[0] == COMPACT_SECTION_FLAG:
            return refpack_decompress(REFPACK_MAGIC + raw[2:])
        if raw[:2] == REFPACK_MAGIC:
            return refpack_decompress(raw)
        return None

    def decompress_all(self) -> None:
        """Decompress every section named in `SECTION_MAP`.

        A section whose bytes are not a RefPack stream is left out of
        `self.sections` and the rest are still read. That is the source's
        behaviour and it is right here for a reason the source did not give: the
        writer only rewrites sections it has a header for, so a table that could
        not be read is a table that will not be written, and one unreadable
        statistics table must not cost a user the roster patch.

        DELIBERATE DIVERGENCE: the source wrapped this loop body in
        `except Exception: pass`. That handler could not fire.
        `decompress_section` returns None rather than raising for every input it
        rejects, and the one function it calls that raises at all --
        `refpack_decompress`, on a missing `0x10 0xFB` header -- is only reached
        after `decompress_section` has checked those two bytes itself. So the
        handler caught nothing and would have hidden any genuine bug in the
        decompressor if one appeared. It is removed rather than narrowed,
        because narrowing it would leave a reader believing a failure mode
        exists here that does not.
        """
        for offset, name in SECTION_MAP:
            data = self.decompress_section(offset)
            if data:
                self.sections[name] = data

    # -- parsing ------------------------------------------------------------

    def parse_csv_section(self, name: str) -> Table:
        """Parse one decompressed section into `{record id: {column: value}}`.

        Also records `self.record_order[name]`, the ids in the order the section
        held them, which is what lets the writer rebuild a table without
        reordering it.

        An id that appears twice keeps its **last** field set and appears
        **twice** in the order, so rebuilding the table emits two rows holding
        that last set. Both halves are the source's behaviour and both are
        preserved: the row count of a table the game may index positionally
        stays what the disc had, and nothing in this package can tell a genuine
        duplicate from a collision in the disc's own key space, so silently
        dropping one would be a decision made in the dark. Pinned by a test, on
        a fixture whose duplicate carries different values in its two rows.
        """
        data = self.sections.get(name)
        if data is None:
            self.record_order[name] = []
            return {}

        records: Table = {}
        order: list[str] = []
        text = data.decode("ascii", errors="replace")

        for line in text.split(RECORD_TERMINATOR):
            line = line.strip()
            if not line:
                continue
            if "," not in line:
                continue
            parts = line.split(",")
            hash_id = parts[0].strip()
            if not _looks_like_record_id(hash_id):
                continue
            fields = _parse_record_body(parts[1:])
            if not fields:
                continue
            records[hash_id] = fields
            order.append(hash_id)

        self.record_order[name] = order
        return records

    def parse_all(self) -> None:
        """Parse every section that decompressed."""
        for _, name in SECTION_MAP:
            if name in self.sections:
                self.records[name] = self.parse_csv_section(name)

    # -- reporting ----------------------------------------------------------

    def get_info(self, *, deep: bool = False) -> MVPRomInfo:
        """Describe the loaded image.

        `deep` chooses which check decides `is_valid`: `validate_deep`, the
        heuristic, or `validate`, the three-byte header test. It does **not**
        decide whether the sections are read -- a valid disc is decompressed and
        parsed either way, because that is where the team slots come from, and
        that is the source's behaviour. `analyze_rom` passes `deep=True`;
        nothing else does.
        """
        data = self.database_big
        if data is None:
            return MVPRomInfo(path=self.iso_path, size=0)

        is_valid = self.validate_deep() if deep else self.validate()
        team_slots: list[MVPTeamSlot] = []
        if is_valid:
            if not self.sections:
                self.decompress_all()
            if not self.records:
                self.parse_all()
            team_slots = self._read_team_slots()

        try:
            iso_size = os.path.getsize(self.iso_path)
        except OSError:
            # The file was readable when `load` ran. If it is not now, report
            # zero rather than raising out of a describe-only method.
            iso_size = 0

        return MVPRomInfo(
            path=self.iso_path,
            size=iso_size,
            database_big_offset=self.database_big_offset,
            database_big_size=len(data),
            team_slots=team_slots,
            is_valid=is_valid,
        )

    def _read_team_slots(self) -> list[MVPTeamSlot]:
        """The 30 team slots, with the roster count and first player the disc has.

        Slot order comes from `MVP_TEAM_ABBREVS`. The source indexed
        `list(TEAM_HASHES.keys())` by slot number here and again in
        `patcher.py`, relying on dict insertion order in two files with nothing
        asserting they agreed; `models.MVP_TEAM_ABBREVS` is the single ordering
        now.

        "First player" is the first roster row the disc lists for the team,
        which is a dict iteration order and therefore the section's own order --
        not a batting order. It is a label for a UI, not a fact about the
        lineup.
        """
        roster_records = self.records.get("roster", {})
        attrib_records = self.records.get("attrib", {})

        team_players: dict[str, list[str]] = {}
        for fields in roster_records.values():
            team_hash = fields.get(ROSTER_TEAMID, "")
            player_hash = fields.get(ROSTER_PLAYERID, "")
            if team_hash and player_hash:
                team_players.setdefault(team_hash, []).append(player_hash)

        slots: list[MVPTeamSlot] = []
        for i in range(TEAM_COUNT):
            abbrev = MVP_TEAM_ABBREVS[i]
            players = team_players.get(TEAM_HASHES.get(abbrev, ""), [])

            first_player = ""
            if players:
                p_fields = attrib_records.get(players[0], {})
                fname = p_fields.get(ATTRIB_FIRST_NAME, "")
                lname = p_fields.get(ATTRIB_LAST_NAME, "")
                if fname or lname:
                    first_player = f"{fname} {lname}".strip()

            slots.append(
                MVPTeamSlot(
                    index=i,
                    name=MVP_TEAM_ORDER[i],
                    abbrev=abbrev,
                    player_count=len(players),
                    first_player=first_player,
                )
            )
        return slots


__all__ = [
    "HASH_ID_CHARS",
    "MVPPSPRomReader",
    "Table",
]
