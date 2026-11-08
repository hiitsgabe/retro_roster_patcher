"""The ported WE2002 writer, driven against a synthetic image.

WE2002 is copyrighted and no disc image may enter this repository, so the file
under the writer here is built in-test and starts out empty. `RomWriter` is
handed the same path for input and output, which takes its in-place branch and
skips the copy; every `write_team` offset is then a seek past the end, so the
result is a sparse ~12 MB file holding a few dozen kilobytes of actual blocks.
Nothing in it came from the game.

That is enough to pin two things nothing else could see: `write_team` writes no
player record, only the links that make a club field the 23 places of its
national slot, so the number it returns is how many of the players handed over
the club fields and not how many it was handed; and `write_flag` gives every
slot a style byte while giving two of the thirty-two no palette at all.
"""

import struct
from types import SimpleNamespace

import pytest

from retro_roster_patcher.games.we2002.models import WELook, WEPlayerRecord, WETeamRecord
from retro_roster_patcher.games.we2002.rom_writer import (
    _ML_COLOR_OFFSETS,
    _OFS_BANDIERE_COLORE1,
    _OFS_BANDIERE_FORMA1,
    _OFS_BANDIERE_FORMA2,
    _OFS_BANDIERE_FORMA3,
    _OFS_BANDIERE_FORMA4,
    _OFS_BANDIERE_FORMA5,
    _SQUADRE_NAZ,
    RomWriter,
    _build_flag_data,
    _encode_player_carat,
    _link_chunks,
)

_FORMA_BASES = (
    _OFS_BANDIERE_FORMA1,
    _OFS_BANDIERE_FORMA2,
    _OFS_BANDIERE_FORMA3,
    _OFS_BANDIERE_FORMA4,
    _OFS_BANDIERE_FORMA5,
)


def _team(name="Team A"):
    return WETeamRecord(name=name, short_name="TMA")


def _players(count):
    """`count` players whose surnames are distinguishable in the raw image.

    `_encode_player_name` uppercases and null-pads to ten bytes, so `L13`
    becomes `b"L13" + 7 nulls` and cannot be confused with `L1`'s
    `b"L1" + 8 nulls`.
    """
    return [
        WEPlayerRecord(last_name=f"L{i}", first_name=f"F{i}", position=2, shirt_number=i + 1)
        for i in range(count)
    ]


def _synthetic_iso(tmp_path):
    """An empty file the writer will grow by seeking; never a real disc image."""
    path = tmp_path / "synthetic.bin"
    path.write_bytes(b"")
    return path


def test_an_overflowing_squad_counts_only_what_the_club_fields(tmp_path):
    # A national slot has 23 places and the club fields all of them, whatever its number, so 23
    # is the most `write_team` reports: `len(players)` is wrong for 25, and so is a capacity of
    # 14 or 15 (the Master League's own player blocks, which nothing reads for a patched club).
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    assert writer.write_team(0, _team(), players=_players(25)) == 23
    assert writer.write_team(31, _team(), players=_players(25)) == 23


def test_a_squad_that_fits_counts_every_player(tmp_path):
    # Below 23 "supplied" and "fielded" agree; the places left over are the dummies
    # `write_nat_team` fills, and must not be counted as patched players.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    assert writer.write_team(0, _team(), players=_players(3)) == 3
    assert writer.write_team(31, _team(), players=_players(22)) == 22


def test_write_team_writes_no_player_record_of_its_own(tmp_path):
    # The club fields its national slot's places, which `write_nat_team` writes; `write_team`
    # only points the club's links there. The image starts empty, so every non-zero byte in it
    # was written by this call, and no player's name is among them.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    writer.write_team(0, _team(), players=_players(22))
    data = path.read_bytes()

    assert not any(f"L{i}".encode() + b"\x00" in data for i in range(22))


def test_a_slot_written_without_a_player_list_counts_no_players(tmp_path):
    # `write_team` still writes names, kits and the flag here; what it does not
    # do is touch the player tables, so the count is zero rather than a capacity.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    assert writer.write_team(0, _team(), include_flag=True) == 0


def test_a_slot_outside_the_master_league_range_counts_nothing(tmp_path):
    # These return before opening the file, so a caller that counted the squad
    # regardless would report a patch that never reached the image.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    assert writer.write_team(32, _team(), players=_players(22)) == 0
    assert writer.write_team(-1, _team(), players=_players(22)) == 0


def test_a_missing_output_file_counts_nothing(tmp_path):
    # The constructor skips its copy when there is nothing to copy, so a writer
    # can legitimately reach `write_team` with no file behind it.
    missing = tmp_path / "gone.bin"
    writer = RomWriter(str(missing), str(missing))

    assert writer.write_team(0, _team(), players=_players(22)) == 0
    assert missing.exists() is False


def test_the_verification_report_reads_the_squad_back_where_the_club_fields_it(tmp_path):
    # The report's read-back follows a club to its national slot: the first player's name there,
    # and the links that lead to it. A report still looking in the Master League's own player
    # blocks would call every patched slot a failure.
    original = tmp_path / "in.bin"
    with original.open("wb") as handle:
        handle.truncate(13_000_000)
    patched = tmp_path / "out.bin"
    writer = RomWriter(str(original), str(patched))
    team = _team()
    team.players = _players(23)
    writer.write_team(2, team, players=team.players)
    writer.write_nat_team(2, team, players=team.players)

    mapping = [SimpleNamespace(slot_index=2)]
    assert "Read-back: 4 OK, 0 FAIL" in writer.verify_patches(str(original), mapping, {2: team})

    # And a club pointed somewhere else is reported.
    with patched.open("r+b") as handle:
        handle.seek(_link_chunks(2)[0][0])
        handle.write(b"\xff\xff")
    report = writer.verify_patches(str(original), mapping, {2: team})
    assert "Read-back: 3 OK, 1 FAIL" in report and "FAIL slot 2 Links" in report


def test_one_master_league_slot_has_no_colour_offset_of_its_own():
    # `_compute_ml_color_offsets` traces the upstream C++ write sequence, which
    # never writes ml[5] or ml[22]; ml[5] was then read off the game (raw slot
    # 36), and ml[22] draws through ml[10]'s palette. The count and the index
    # are asserted separately because the comments in `rom_writer.py` state
    # both, and a table that lost a different slot would still have 31 entries.
    assert len(_ML_COLOR_OFFSETS) == 31
    assert sorted(set(range(32)) - set(_ML_COLOR_OFFSETS)) == [22]
    assert _ML_COLOR_OFFSETS[5] == [(_OFS_BANDIERE_COLORE1 + 678, 32)]


def test_a_slot_with_a_colour_offset_gets_a_style_byte_and_a_palette(tmp_path):
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))
    team = _team()

    writer.write_flag(4, team)
    data = path.read_bytes()

    style, colour = _build_flag_data(team)
    assert [data[base + _SQUADRE_NAZ + 4] for base in _FORMA_BASES] == [style] * 5
    ((offset, size),) = _ML_COLOR_OFFSETS[4]
    assert size == 32
    assert data[offset : offset + size] == colour


@pytest.mark.parametrize("palette", [None, [(200, 100, 50)] * 16], ids=["stripes", "palette"])
def test_a_flag_never_paints_the_next_slot_s_see_through_index(palette):
    """A colour entry's 16th halfword is the next slot's index 0, which draws see-through: the
    striped fallback wrote its second colour there, over the edge of Australia's flag when
    Ivory Coast in slot 52 got stripes."""
    team = _team()
    team.kit_home, team.kit_away = (200, 0, 0), (0, 0, 200)
    team.flag_palette = palette
    team.flag_style = 7 if palette else None
    _style, colour = _build_flag_data(team)
    halfwords = struct.unpack("<16H", colour)
    assert halfwords[15] == 0
    if palette is None:
        assert len(set(halfwords[:8])) == 1 and len(set(halfwords[8:15])) == 1


def test_a_slot_with_no_colour_offset_gets_the_style_byte_and_no_palette(tmp_path):
    # The consequence the `rom_writer.py` comments have to state: slot 22 takes
    # a new geometric flag pattern over the palette slot 10 was given, because
    # the game draws it through slot 10's CLUT, and `patcher.py` counts it as
    # patched. Writing colour bytes for it would land on slot 10's palette.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))
    team = _team()

    writer.write_flag(22, team)
    data = path.read_bytes()

    style, _ = _build_flag_data(team)
    assert [data[base + _SQUADRE_NAZ + 22] for base in _FORMA_BASES] == [style] * 5
    # The image started empty, so the five style bytes are the only thing in it:
    # 32 palette bytes would have gone somewhere, and there is nowhere for them.
    assert len(data) - data.count(b"\x00") == 5
    assert path.stat().st_size == max(_FORMA_BASES) + _SQUADRE_NAZ + 22 + 1


def test_the_slot_that_straddles_a_sector_boundary_is_written_in_two_pieces(tmp_path):
    # ml[26] is the one slot whose 32 colour bytes do not sit together: 26 bytes
    # end one sector and 6 begin the next, at `_OFS_BANDIERE_COLORE2`. Writing
    # it as one run would put six bytes into whatever follows the first sector.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))
    team = _team()

    writer.write_flag(26, team)
    data = path.read_bytes()

    _, colour = _build_flag_data(team)
    chunks = _ML_COLOR_OFFSETS[26]
    assert [size for _, size in chunks] == [26, 6]
    head, tail = chunks
    assert data[head[0] : head[0] + 26] == colour[:26]
    assert data[tail[0] : tail[0] + 6] == colour[26:]


# ---------------------------------------------------------------------------
# The national 2D kit preview table and its sector boundary.
# ---------------------------------------------------------------------------


def test_the_national_preview_table_holds_thirty_one_records_before_the_boundary():
    # The table starts 64 bytes into sector 1134's user data, so records 0-30
    # fit before its EDC; 31 is the first in the next sector. A split at 30 put
    # every national team from 30 up one record off and 62 into the EDC.
    from retro_roster_patcher.games.we2002.rom_writer import (
        _OFS_ANT_MAGLIE,
        _OFS_ANT_MAGLIE1,
        _nat_jersey_offset,
    )

    assert _OFS_ANT_MAGLIE - (2_667_168 + 24) == 64
    assert _nat_jersey_offset(30) == _OFS_ANT_MAGLIE + 30 * 64
    assert _nat_jersey_offset(30) + 64 == 2_667_168 + 24 + 2048  # the sector's user data ends
    assert _nat_jersey_offset(31) == _OFS_ANT_MAGLIE1
    assert _nat_jersey_offset(62) == _OFS_ANT_MAGLIE1 + 31 * 64


def test_the_reader_and_the_writer_agree_on_where_national_team_31_is(tmp_path):
    from retro_roster_patcher.games.we2002.rom_reader import RomReader

    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))
    writer.write_nat_team(31, WETeamRecord(name="N", short_name="N", kit_home=(255, 0, 0)))
    palettes = {
        p.slot_index: p
        for p in RomReader(str(path)).read_slot_palettes()
        if p.slot_type == "national"
    }
    assert palettes[31].primary == (255, 0, 0)
    assert palettes[30].primary == (0, 0, 0)


def test_the_carat_bits_carry_the_look_where_the_editor_source_puts_them():
    look = WELook(skin=3, hair_style=21, hair_colour=7, beard=2, beard_colour=7, build=4)
    record = WEPlayerRecord("A", "B", 2, 9, height_cm=190, age=31, look=look)
    c = _encode_player_carat(record)
    assert (c[0] >> 4) | ((c[1] & 1) << 4) == 21 and (c[1] >> 1) & 7 == 7 and (c[1] >> 5) & 7 == 2
    assert (c[2] >> 1) & 7 == 7 and 148 + ((c[2] >> 4) | ((c[3] & 3) << 4)) == 190
    assert c[4] & 3 == 3 and (c[4] >> 2) & 7 == 4 and 15 + ((c[4] >> 5) | ((c[5] & 3) << 3)) == 31
    assert ((c[3] >> 2) & 0x1F) + 1 == 9 and c[0] & 7 == 3  # the number and position still land


def test_height_and_age_are_clamped_not_wrapped():
    tall = _encode_player_carat(WEPlayerRecord("A", "B", 2, 1, height_cm=215, age=16))
    assert 148 + ((tall[2] >> 4) | ((tall[3] & 3) << 4)) == 202
    assert 15 + ((tall[4] >> 5) | ((tall[5] & 3) << 3)) == 18
    short = _encode_player_carat(WEPlayerRecord("A", "B", 2, 1, height_cm=140, age=44))
    assert 148 + ((short[2] >> 4) | ((short[3] & 3) << 4)) == 155
    assert 15 + ((short[4] >> 5) | ((short[5] & 3) << 3)) == 40
