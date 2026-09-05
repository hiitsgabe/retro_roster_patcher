"""Read `database.big` out of an MVP Baseball (PSP) ISO and parse its CSV tables.

    ISO -> seek(DATABASE_BIG_LBA * 2048) -> 19 RefPack streams -> 19 CSV tables

No ISO 9660 walk: `models.DATABASE_BIG_LBA` says where the file is and the
reader seeks there. Do not import `formats/iso9660.py` here; see `models`.

A record's id is not its position. Records are keyed by a nine-hex-digit id and
`record_order` keeps the sequence separately, because the game's own indices
into these tables are positional even though the links between tables are not.

The header line is skipped by shape, not by position: a section's first line
names its columns, every later line starts with a hex id, and
`_looks_like_record_id` is the test.

Values are not stripped. `"0 "` is field 0 holding the empty string, which is
different from field 0 being absent, and the split is on the *first* space only
so a value containing spaces survives.
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
# does not implement; `decompress_section` replaces those two bytes with
# `10 FB` and passes byte 2 onwards through unchanged, so the fixup assumes the
# 0xC0 header is the same five bytes as the 0x10 one. If 0xC0 really introduces
# a compressed-size word ahead of the decompressed size, bytes 2-4 are that word
# and the size the decompressor reads is wrong. Unverified: no disc may enter
# this repository to check which header a real section 0 has.
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

    The disc uses nine lower-case hex digits, but the test is the looser "at
    least five characters, all of them hex": a shorter id is not something this
    repository can rule out, and rejecting one would silently drop a record.
    """
    if len(text) < 5:
        return False
    return all(c in HASH_ID_DIGITS for c in text)


def _parse_record_body(parts: list[str]) -> dict[int, str]:
    """Turn `["0 Ichiro", "1 Suzuki", "22 61", ""]` into `{0: ..., 1: ..., 22: ...}`.

    Each part is a column number, one space, then the value. The split is on the
    first space so a value may contain spaces; the value is *not* stripped, so
    `"0 "` records column 0 as the empty string rather than dropping it.

    A blank part is skipped -- that is what the trailing `,;` leaves -- and so
    is a part with no space in it, and one whose column number is not an integer.
    """
    fields: dict[int, str] = {}
    for part in parts:
        # Redundant with the two guards below, and kept: "a blank part is not a
        # column" is the fact, and reaching it through an integer parse that
        # happens to fail is not.
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

    def load(self) -> bool:
        """Read `database.big` out of the image.

        False, without raising, for a file that does not exist, cannot be read,
        or is shorter than the extent. `patcher._database_big_extent_fits`
        checks the last of those again on purpose: one boolean for four reasons
        cannot tell a user *which* reason applies.

        Never accept a short read: `refpack_decompress` returns short for a
        truncated stream and never pads, so a partial `database.big` would
        rebuild every later section from a truncated table and report success.
        """
        if not os.path.exists(self.iso_path):
            return False
        offset, _ = database_big_extent()
        try:
            with open(self.iso_path, "rb") as f:
                f.seek(offset)
                data = f.read(DATABASE_BIG_SIZE)
        except OSError:
            # `OSError` and not `Exception`: a seek or a read raises `OSError`,
            # and anything else here is a bug in this module that must not be
            # reported as "not this game".
            return False
        if len(data) < DATABASE_BIG_SIZE:
            return False
        self.database_big = data
        self.database_big_offset = offset
        return True

    def validate(self) -> bool:
        """Does the loaded blob look like this game's `database.big`?

        Three bytes: offset 0 is 0x10 or 0xC0, and offsets 324-325 are 0x10 0xFB.
        The cheap gate only; `analyze_rom` decides on `validate_deep`.

        The reads are unguarded because `load` refuses anything that did not
        yield all 386 977 bytes.
        """
        data = self.database_big
        if data is None:
            return False
        if data[0] not in VALID_FIRST_BYTES:
            return False
        return data[ATTRIB_SECTION_OFFSET : ATTRIB_SECTION_OFFSET + 2] == REFPACK_MAGIC

    def validate_deep(self) -> bool:
        """`validate`, and then: does the `team` table hold MVP Baseball's teams?

        A heuristic, and it guards `analyze_rom` only. `validate`'s three bytes
        -- a byte in `{0x10, 0xC0}`, then 0x10 0xFB -- separate this game from
        noise and not from its siblings: on other EA PSP discs of the era a
        RefPack stream at a sector boundary is the house format, not a
        coincidence. The team ids do separate it, being this database's own
        primary keys.

        One match is enough, not thirty: a disc a previous patcher touched, or a
        regional variant that dropped a club, must still be recognised.
        """
        if not self.validate():
            return False
        if not self.sections:
            self.decompress_all()
        if not self.records:
            self.parse_all()
        team_ids = self.records.get("team", {})
        return any(team_hash in team_ids for team_hash in TEAM_HASHES.values())

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
        # Redundant with `len(raw) < 2` below, and kept: "the offset is not in
        # the blob" is a different statement from "there were not two bytes
        # there", and this one is the true one.
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
        `self.sections` and the rest are still read: the writer only rewrites
        sections it has a header for, so one unreadable statistics table must
        not cost a user the roster patch.

        Deliberately unguarded, where upstream wrapped the loop body in
        `except Exception: pass`. `decompress_section` can raise from
        underneath -- `refpack_decompress` refuses a stream shorter than five
        bytes and only the two header bytes are checked here -- but this loop
        passes offsets from `SECTION_MAP`, the last of which is 385 608 in a
        blob of 386 977, so its shortest slice is 1369 bytes. Do not restore the
        handler: it caught nothing and would hide a real decompressor bug.
        """
        for offset, name in SECTION_MAP:
            data = self.decompress_section(offset)
            if data:
                self.sections[name] = data

    def parse_csv_section(self, name: str) -> Table:
        """Parse one decompressed section into `{record id: {column: value}}`.

        Also records `self.record_order[name]`, the ids in the order the section
        held them, which is what lets the writer rebuild a table without
        reordering it.

        An id that appears twice keeps its **last** field set and appears
        **twice** in the order, so rebuilding emits two rows holding that last
        set. Preserve both halves: the row count of a table the game may index
        positionally stays what the disc had, and nothing here can tell a
        genuine duplicate from a collision in the disc's own key space.
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
            # Redundant with the two guards below, and kept: it is the cheap
            # test and it names the shape a record has.
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

    def get_info(self, *, deep: bool = False) -> MVPRomInfo:
        """Describe the loaded image.

        `deep` chooses which check decides `is_valid`: `validate_deep`, the
        heuristic, or `validate`, the three-byte header test. It does not decide
        whether the sections are read -- a valid disc is decompressed and parsed
        either way, because that is where the team slots come from.
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

        Slot order comes from `MVP_TEAM_ABBREVS`. Never index `TEAM_HASHES` by
        slot number: that relies on dict insertion order agreeing across files.

        "First player" is the first roster row the disc lists for the team, so
        it is the section's own order and not a batting order.
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
