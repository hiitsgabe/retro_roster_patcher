"""Fabricate a structurally valid Ken Griffey Jr. Presents MLB (SNES) ROM in memory.

Nothing here is derived from a real ROM. Every byte is computed from the format
`games/kgj_mlb_snes/rom_reader.py` and `rom_writer.py` document, and the layout
constants below are chosen to be legal rather than to match a real dump.

Why the image is exactly 2 MB, or exactly 2 MB + 512
----------------------------------------------------
`KGJRomReader.validate` accepts those two sizes and nothing else -- not a floor,
not a band. The headered variant is the headerless one with 512 bytes of copier
header stuck on the front, which is what a real `.smc` is.

Why the marker's position is a parameter
----------------------------------------
There is no fixed offset to the team tables. `validate` searches the whole image
for a 14-byte marker and takes the byte after it as team 0's first player. That
is what makes the copier header cost nothing: prepending 512 bytes moves the
marker by 512 and everything derived from it follows. It is also the one thing
`validate` does not bound, so `marker_offset` is a parameter here: a builder
call that puts it near the end of the file produces the image
`patcher._team_data_fits` refuses.

Why the constants here are literals and not imports
---------------------------------------------------
The marker, the record field offsets and the character encoding are
retranscribed below rather than imported from `games.kgj_mlb_snes.models`. That
duplication is the point: a test that located a field using the very constant it
is meant to pin would move with the constant and assert nothing. Moving
`AL_TO_NL_GAP`, or shifting `CHAR_TO_BYTE` by one, must break tests here, and it
only can if these are independent transcriptions.

`encode_char` is written as the *rule* the table encodes -- space at 0, the ten
digits from 0x01, A-Z from 0x0B, and a lone lowercase `c` at 0x36 -- rather than
as a copy of the table, so it disagrees with any renumbering of it.

Why every byte in a record is unique to its (team, slot)
--------------------------------------------------------
Each player's name, jersey, position, attributes and stats encode both the team
index and the roster slot. Uniform filler would make the 700 records identical,
and then no assertion could tell which record a read or a write landed on: a
writer that ignored `team_index`, or filled a roster in reverse, or confused the
AL and NL halves, would satisfy every equality a test could write.

Why the filler is pseudo-random rather than zero
------------------------------------------------
`KGJRomWriter.update_snes_checksum` sums every byte in the file. Against a
zero-filled image that sum is a constant, and starting it at a different offset,
or over a different length, produces the same number -- so the test would pass
against a checksum routine that summed nothing at all. The filler below is a
deterministic LCG, so every byte of the image contributes.
"""

from __future__ import annotations

import pathlib

#: The real cartridge size: 16 Mbit, headerless.
ROM_SIZE = 0x200000

#: The copier header a `.smc` carries in front of that.
SMC_HEADER_SIZE = 512

#: Independent transcription of `models.FIRST_TEAM_MARKER`. 14 bytes, and every
#: one of them is >= 0x81, which is why the filler below never collides with it
#: by accident.
FIRST_TEAM_MARKER = bytes(
    [0x81, 0x81, 0x81, 0x81, 0x9F, 0x9F, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0xF0, 0xF0]
)

#: Where the marker is planted by default. Chosen to clear the SNES header at
#: 0x7FC0 and to leave far more than `TEAM_DATA_SPAN` bytes behind it.
MARKER_OFFSET = 0x100000

TEAM_COUNT = 28
AL_TEAMS = 14
PLAYERS_PER_TEAM = 25
BATTERS_PER_TEAM = 15
STARTERS_PER_TEAM = 5

#: Independent transcription of the ROM layout constants.
PLAYER_LENGTH = 0x20
TEAM_LENGTH = 0x320
AL_TO_NL_GAP = 0xB40

#: Bytes of team data that follow the end of the marker. Computed here from this
#: module's own constants, not imported: 14 AL blocks, the gap, 14 NL blocks.
TEAM_DATA_SPAN = AL_TEAMS * TEAM_LENGTH + AL_TO_NL_GAP + (TEAM_COUNT - AL_TEAMS) * TEAM_LENGTH

#: SNES internal checksum and its complement, in the headerless image's own
#: coordinates. `update_snes_checksum` shifts both by 512 for a headered file.
CHECKSUM_OFFSET = 0x7FDE
COMPLEMENT_OFFSET = 0x7FDC

#: Deliberately not the correct checksum for this image, so a test can tell
#: "recomputed" from "left alone".
CHECKSUM_FILLER = bytes([0xAD, 0xDE])
COMPLEMENT_FILLER = bytes([0xEF, 0xBE])

#: Independent transcription of the 32-byte record layout.
OFF_FIRST_INITIAL = 0x00
OFF_LAST_NAME = 0x01
LAST_NAME_LENGTH = 8
OFF_POSITION = 0x09
OFF_JERSEY = 0x0A
OFF_ATTR_PAIR = 0x0B
OFF_ATTR_SECOND = 0x0C
OFF_BAT_HAND = 0x0D
OFF_SKIN_HEAD = 0x0E
OFF_HAIR_BODY = 0x0F
OFF_LEGS = 0x10
OFF_ARMS = 0x11
OFF_PITCH_HAND_SKIN = 0x15
OFF_PITCH_HEAD_HAIR = 0x16
OFF_PITCH_BODY_STYLE = 0x17
OFF_STAT_FIRST = 0x18
OFF_ROSTER_TYPE = 0x19
OFF_STAT_SECOND = 0x1A
OFF_ALWAYS_ZERO = 0x1B
OFF_STAT_THIRD = 0x1C
OFF_KIND_FLAG = 0x1D
OFF_STAT_FOURTH = 0x1E

#: Record bytes no writer in this package ever touches. Filled non-zero so a
#: write that strayed one byte past a field it does own is visible.
UNTOUCHED_OFFSETS = (0x12, 0x13, 0x14, 0x1F)

#: Independent transcription of `models.POSITION_TO_BYTE`, in its own order.
POSITION_BYTES = {
    "P": 0x00,
    "C": 0x02,
    "LF": 0x04,
    "CF": 0x06,
    "RF": 0x08,
    "3B": 0x0A,
    "SS": 0x0C,
    "2B": 0x0E,
    "1B": 0x10,
    "DH": 0x12,
    "IF": 0x14,
    "OF": 0x16,
}
BYTES_TO_POSITION = {value: key for key, value in POSITION_BYTES.items()}

#: The nine lineup positions a batter slot cycles through, plus the two generic
#: ones, so every value of `POSITION_BYTES` except "P" appears across a roster.
BATTER_POSITIONS = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "IF", "OF"]

#: Independent transcription of `models.HAND_RIGHT`/`HAND_LEFT`/`HAND_SWITCH`.
BAT_HANDS = (0x00, 0x11, 0x20)

#: The high nibble of byte 0x19: 3 for a batter, 1 for a starting pitcher, 0 for
#: a reliever. Retranscribed from what `read_player` decodes.
ROSTER_TYPE_BATTER = 3
ROSTER_TYPE_STARTER = 1
ROSTER_TYPE_RELIEVER = 0


def encode_char(ch: str) -> int:
    """The name encoding, stated as a rule rather than copied as a table.

    Space is 0x00, the digits run from 0x01, A-Z runs from 0x0B, and lowercase
    `c` alone is 0x36 -- the one lowercase letter the ROM font table in
    `models.CHAR_TO_BYTE` names, so that "McGWIRE" renders. Everything else is
    0x00, which is a SPACE and not a terminator.
    """
    if ch == " ":
        return 0x00
    if "0" <= ch <= "9":
        return 0x01 + (ord(ch) - ord("0"))
    if "A" <= ch <= "Z":
        return 0x0B + (ord(ch) - ord("A"))
    if ch == "c":
        return 0x36
    return 0x00


def decode_char(value: int) -> str:
    """Inverse of `encode_char`. Unmapped bytes render as "?", as the reader does."""
    if value == 0x00:
        return " "
    if 0x01 <= value <= 0x0A:
        return chr(ord("0") + value - 0x01)
    if 0x0B <= value <= 0x24:
        return chr(ord("A") + value - 0x0B)
    if value == 0x36:
        return "c"
    return "?"


def encode_name(name: str, length: int = LAST_NAME_LENGTH) -> bytes:
    """Encode and pad a name to `length` bytes."""
    encoded = [encode_char(ch) for ch in name[:length]]
    encoded += [0x00] * (length - len(encoded))
    return bytes(encoded)


def decode_name(raw: bytes | bytearray) -> str:
    """Decode a padded name field and strip the padding."""
    return "".join(decode_char(b) for b in raw).strip()


def team_offset(team: int, *, first_team_offset: int) -> int:
    """Absolute file offset of a team's 800-byte block.

    Retranscribed from what `rom_reader.get_team_offset` documents: the 14 AL
    blocks are contiguous from the marker, then 2 880 bytes of something else,
    then the 14 NL blocks.
    """
    if team < AL_TEAMS:
        return first_team_offset + team * TEAM_LENGTH
    return (
        first_team_offset + AL_TEAMS * TEAM_LENGTH + AL_TO_NL_GAP + (team - AL_TEAMS) * TEAM_LENGTH
    )


def player_offset(team: int, slot: int, *, first_team_offset: int) -> int:
    """Absolute file offset of one 32-byte player record."""
    return team_offset(team, first_team_offset=first_team_offset) + slot * PLAYER_LENGTH


def player_last_name(team: int, slot: int) -> str:
    """The last name in one roster slot, encoding both coordinates.

    Six characters in an eight-byte field, so the two padding bytes are part of
    what a test can assert on.
    """
    return f"T{team:02d}P{slot:02d}"


def player_first_initial(team: int, slot: int) -> str:
    """One letter, varying with both coordinates."""
    return chr(ord("A") + (team * 3 + slot) % 26)


def is_pitcher_slot(slot: int) -> bool:
    """Slots 15-24 hold pitchers; 0-14 hold batters."""
    return slot >= BATTERS_PER_TEAM


def roster_type_nibble(slot: int) -> int:
    if slot < BATTERS_PER_TEAM:
        return ROSTER_TYPE_BATTER
    if slot < BATTERS_PER_TEAM + STARTERS_PER_TEAM:
        return ROSTER_TYPE_STARTER
    return ROSTER_TYPE_RELIEVER


def player_position(team: int, slot: int) -> str:
    if is_pitcher_slot(slot):
        return "P"
    return BATTER_POSITIONS[(team + slot) % len(BATTER_POSITIONS)]


def player_jersey(team: int, slot: int) -> int:
    """1-99. Never 0, so a record the writer skipped is distinguishable."""
    return 1 + (team * 5 + slot) % 99


def player_bat_hand(team: int, slot: int) -> int:
    return BAT_HANDS[(team + slot) % len(BAT_HANDS)]


def _rating(team: int, slot: int, salt: int) -> int:
    """A 1-10 attribute value, distinct per (team, slot, field)."""
    return 1 + (team * 7 + slot * 13 + salt * 3) % 10


def player_ratings(team: int, slot: int) -> tuple[int, int, int, int]:
    """The four 1-10 values a record carries, in field order.

    For a batter they are (batting, power, speed, defense); for a pitcher the
    first three are (speed, control, fatigue) and the fourth is unused. Every
    one is distinct within the record, so a swapped nibble pair is visible.
    """
    return (
        _rating(team, slot, 0),
        _rating(team, slot, 1),
        _rating(team, slot, 2),
        _rating(team, slot, 3),
    )


def player_batting_avg(team: int, slot: int) -> int:
    """0-999, and always above 255 so the high nibble at 0x19 is non-zero."""
    return 300 + (team * 11 + slot) % 60


def player_era(team: int, slot: int) -> int:
    """0-999, and always above 255 so the high nibble at 0x1D is non-zero."""
    return 280 + (team * 13 + slot) % 90


def _appearance(team: int, slot: int, salt: int) -> int:
    """A 0-15 nibble, never 0, distinct per (team, slot, field)."""
    return 1 + (team * 3 + slot * 5 + salt) % 15


def build_player_record(team: int, slot: int) -> bytes:
    """One complete 32-byte record for a (team, slot)."""
    record = bytearray(PLAYER_LENGTH)
    record[OFF_FIRST_INITIAL] = encode_char(player_first_initial(team, slot))
    record[OFF_LAST_NAME : OFF_LAST_NAME + LAST_NAME_LENGTH] = encode_name(
        player_last_name(team, slot)
    )
    record[OFF_POSITION] = POSITION_BYTES[player_position(team, slot)]
    record[OFF_JERSEY] = player_jersey(team, slot)

    first, second, third, fourth = player_ratings(team, slot)
    record[OFF_BAT_HAND] = player_bat_hand(team, slot)
    record[OFF_SKIN_HEAD] = (_appearance(team, slot, 1) << 4) | _appearance(team, slot, 2)
    record[OFF_HAIR_BODY] = (_appearance(team, slot, 3) << 4) | _appearance(team, slot, 4)
    record[OFF_LEGS] = (_appearance(team, slot, 5) << 4) | _appearance(team, slot, 6)
    record[OFF_ARMS] = (_appearance(team, slot, 7) << 4) | _appearance(team, slot, 8)
    for offset in UNTOUCHED_OFFSETS:
        record[offset] = 0x40 | ((team + slot + offset) % 0x3F)

    nibble = roster_type_nibble(slot)
    if is_pitcher_slot(slot):
        record[OFF_ATTR_PAIR] = ((first - 1) << 4) | (second - 1)
        record[OFF_ATTR_SECOND] = third - 1
        record[OFF_PITCH_HAND_SKIN] = ((slot % 2) << 4) | _appearance(team, slot, 9)
        record[OFF_PITCH_HEAD_HAIR] = (_appearance(team, slot, 10) << 4) | _appearance(
            team, slot, 11
        )
        record[OFF_PITCH_BODY_STYLE] = (_appearance(team, slot, 12) << 4) | (slot % 2)
        record[OFF_STAT_FIRST] = 1 + (team + slot) % 30  # wins
        record[OFF_ROSTER_TYPE] = nibble << 4
        record[OFF_STAT_SECOND] = 1 + (team * 2 + slot) % 25  # losses
        # Non-zero, and the writer writes 0 here: that is what makes the write
        # observable rather than a no-op against matching filler.
        record[OFF_ALWAYS_ZERO] = 0x5A
        era = player_era(team, slot)
        record[OFF_STAT_THIRD] = era & 0xFF
        record[OFF_KIND_FLAG] = 0x20 | ((era >> 8) & 0x0F)
        record[OFF_STAT_FOURTH] = 1 + (team * 3 + slot) % 45  # saves
    else:
        record[OFF_ATTR_PAIR] = ((first - 1) << 4) | (second - 1)
        record[OFF_ATTR_SECOND] = ((third - 1) << 4) | (fourth - 1)
        # Non-zero, and the writer zeroes all three for a batter.
        record[OFF_PITCH_HAND_SKIN] = 0x71
        record[OFF_PITCH_HEAD_HAIR] = 0x72
        record[OFF_PITCH_BODY_STYLE] = 0x73
        avg = player_batting_avg(team, slot)
        record[OFF_STAT_FIRST] = avg & 0xFF
        record[OFF_ROSTER_TYPE] = (nibble << 4) | ((avg >> 8) & 0x0F)
        record[OFF_STAT_SECOND] = 1 + (team * 4 + slot) % 50  # home runs
        record[OFF_ALWAYS_ZERO] = 0x5B
        record[OFF_STAT_THIRD] = 1 + (team * 6 + slot) % 130  # RBI
        record[OFF_KIND_FLAG] = 0x10
        # Non-zero, and the writer writes 0 here for a batter.
        record[OFF_STAT_FOURTH] = 0x6C
    return bytes(record)


def decode_player_record(rom: bytes | bytearray, offset: int) -> dict:
    """Split one 32-byte record back into the fields it carries.

    Transcribed from the layout `models.py` documents, deliberately not from
    `KGJRomReader.read_player`: a decoder that called the reader would agree with
    any rearrangement of the reader's own offsets.

    Every field is reported for every record, batter and pitcher alike, because
    which of the two a record is depends on the roster-type nibble the test may
    be trying to pin.
    """
    raw = bytes(rom[offset : offset + PLAYER_LENGTH])
    era_or_rbi = raw[OFF_STAT_THIRD]
    return {
        "raw": raw,
        "first_initial": decode_char(raw[OFF_FIRST_INITIAL]),
        "last_name": decode_name(raw[OFF_LAST_NAME : OFF_LAST_NAME + LAST_NAME_LENGTH]),
        "position": BYTES_TO_POSITION.get(raw[OFF_POSITION], "?"),
        "position_byte": raw[OFF_POSITION],
        "jersey": raw[OFF_JERSEY],
        "attr_high": (raw[OFF_ATTR_PAIR] >> 4) + 1,
        "attr_low": (raw[OFF_ATTR_PAIR] & 0xF) + 1,
        "attr2_high": (raw[OFF_ATTR_SECOND] >> 4) + 1,
        "attr2_low": (raw[OFF_ATTR_SECOND] & 0xF) + 1,
        "bat_hand": raw[OFF_BAT_HAND],
        "skin_head": raw[OFF_SKIN_HEAD],
        "hair_body": raw[OFF_HAIR_BODY],
        "legs": raw[OFF_LEGS],
        "arms": raw[OFF_ARMS],
        "untouched": {offset: raw[offset] for offset in UNTOUCHED_OFFSETS},
        "pitch_hand_skin": raw[OFF_PITCH_HAND_SKIN],
        "pitch_head_hair": raw[OFF_PITCH_HEAD_HAIR],
        "pitch_body_style": raw[OFF_PITCH_BODY_STYLE],
        "roster_type": (raw[OFF_ROSTER_TYPE] >> 4) & 0xF,
        "kind_flag": raw[OFF_KIND_FLAG],
        "always_zero": raw[OFF_ALWAYS_ZERO],
        "stat_fourth": raw[OFF_STAT_FOURTH],
        # A batting average for a batter and a win total for a pitcher.
        "batting_avg": ((raw[OFF_ROSTER_TYPE] & 0x0F) << 8) | raw[OFF_STAT_FIRST],
        "wins": raw[OFF_STAT_FIRST],
        "losses": raw[OFF_STAT_SECOND],
        "home_runs": raw[OFF_STAT_SECOND],
        "era": ((raw[OFF_KIND_FLAG] & 0x0F) << 8) | era_or_rbi,
        "rbi": era_or_rbi,
        "saves": raw[OFF_STAT_FOURTH],
    }


#: One 2 MB LCG run costs about a fifth of a second, and this module builds an
#: image per test. The run is deterministic, so the longest one ever asked for is
#: kept and every shorter request is its prefix -- which also makes a headered
#: image's 512-byte header the same bytes as the head of its body, so a test that
#: confused the two would still see them differ from the checksum filler.
_FILLER_CACHE = bytearray()


def _filler(size: int) -> bytearray:
    """A deterministic pseudo-random fill, every byte in 0x01-0x7F.

    Below 0x80 by construction, and every byte of `FIRST_TEAM_MARKER` is at or
    above 0x81, so the filler cannot contain the marker however long the image
    is. `build_kgj_rom` still checks, because the player records written over
    this filler are not so constrained.
    """
    global _FILLER_CACHE
    if len(_FILLER_CACHE) < size:
        data = bytearray(size)
        state = 0x1234_5678
        for index in range(size):
            state = (state * 1103515245 + 12345) & 0x7FFF_FFFF
            data[index] = 0x01 + ((state >> 16) % 0x7F)
        _FILLER_CACHE = data
    return bytearray(_FILLER_CACHE[:size])


def build_kgj_rom(
    *,
    size: int = ROM_SIZE,
    marker_offset: int = MARKER_OFFSET,
    with_header: bool = False,
    teams: int = TEAM_COUNT,
    players_per_team: int = PLAYERS_PER_TEAM,
    place_marker: bool = True,
) -> bytearray:
    """Return an image the reader accepts and the writer can fill.

    `size` is the size of the body, before any copier header: `with_header`
    prepends 512 more bytes and shifts everything, marker included, which is the
    relationship `validate` exploits to need no header arithmetic.

    `marker_offset` is a body-relative offset. Placing it within
    `TEAM_DATA_SPAN` of the end produces the image `_team_data_fits` refuses.
    `place_marker=False` produces a correctly-sized image with no marker at all,
    which is what `validate` refuses.

    `teams` and `players_per_team` cut how many records are written; the rest of
    the team area keeps its filler, which is not a valid record.
    """
    body = _filler(size)

    if size >= CHECKSUM_OFFSET + 2:
        body[CHECKSUM_OFFSET : CHECKSUM_OFFSET + 2] = CHECKSUM_FILLER
        body[COMPLEMENT_OFFSET : COMPLEMENT_OFFSET + 2] = COMPLEMENT_FILLER

    if place_marker:
        body[marker_offset : marker_offset + len(FIRST_TEAM_MARKER)] = FIRST_TEAM_MARKER
        first_team_offset = marker_offset + len(FIRST_TEAM_MARKER)
        for team in range(teams):
            for slot in range(players_per_team):
                offset = player_offset(team, slot, first_team_offset=first_team_offset)
                if offset + PLAYER_LENGTH > size:
                    continue
                body[offset : offset + PLAYER_LENGTH] = build_player_record(team, slot)

    if with_header:
        rom = _filler(SMC_HEADER_SIZE) + body
    else:
        rom = body

    if place_marker:
        expected = marker_offset + (SMC_HEADER_SIZE if with_header else 0)
        found = bytes(rom).find(FIRST_TEAM_MARKER)
        if found != expected:
            raise AssertionError(
                f"synthetic image has the marker at {found:#x}, not {expected:#x}: "
                "a player record collided with it"
            )
    return rom


def write_kgj_rom(
    path: pathlib.Path,
    *,
    size: int = ROM_SIZE,
    marker_offset: int = MARKER_OFFSET,
    with_header: bool = False,
    teams: int = TEAM_COUNT,
    players_per_team: int = PLAYERS_PER_TEAM,
    place_marker: bool = True,
) -> pathlib.Path:
    """Write a synthetic ROM to `path` and return it."""
    path.write_bytes(
        bytes(
            build_kgj_rom(
                size=size,
                marker_offset=marker_offset,
                with_header=with_header,
                teams=teams,
                players_per_team=players_per_team,
                place_marker=place_marker,
            )
        )
    )
    return path
