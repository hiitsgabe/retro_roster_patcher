"""The ported WE2002 writer, driven against a synthetic image.

WE2002 is copyrighted and no disc image may enter this repository, so the file
under the writer here is built in-test and starts out empty. `RomWriter` is
handed the same path for input and output, which takes its in-place branch and
skips the copy; every `write_team` offset is then a seek past the end, so the
result is a sparse ~12 MB file holding a few dozen kilobytes of actual blocks.
Nothing in it came from the game.

That is enough to pin two things nothing else could see: `write_team` writes a
fixed number of players per slot and drops the rest, so the number it returns is
not the number it was handed; and `write_flag` gives every slot a style byte
while giving two of the thirty-two no palette at all.
"""

from retro_roster_patcher.games.we2002.models import WEPlayerRecord, WETeamRecord
from retro_roster_patcher.games.we2002.rom_writer import (
    _ML_COLOR_OFFSETS,
    _OFS_BANDIERE_FORMA1,
    _OFS_BANDIERE_FORMA2,
    _OFS_BANDIERE_FORMA3,
    _OFS_BANDIERE_FORMA4,
    _OFS_BANDIERE_FORMA5,
    _SQUADRE_NAZ,
    RomWriter,
    _build_flag_data,
    _slot_player_range,
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


def test_an_overflowing_squad_counts_only_what_the_slot_holds(tmp_path):
    # 22 is the squad size the mapper produces for a full first team, and both
    # answers are wrong in a different direction: `len(players)` says 22 for both
    # slots, and a single hard-coded capacity says 14 for both or 15 for both.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    assert writer.write_team(0, _team(), players=_players(22)) == 14
    assert writer.write_team(31, _team(), players=_players(22)) == 15


def test_a_squad_that_fits_counts_every_player(tmp_path):
    # Three is below both capacities, so this is the branch where "supplied" and
    # "written" legitimately agree — the remaining eleven places are dummy-filled
    # and must not be counted as patched players.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    assert writer.write_team(0, _team(), players=_players(3)) == 3


def test_the_players_past_the_capacity_never_reach_the_image(tmp_path):
    # The arithmetic above would still pass if the writer wrote all 22 records
    # and merely reported 14. The image is the evidence: it starts empty, so
    # every non-zero byte in it was written by this call.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    writer.write_team(0, _team(), players=_players(22))
    data = path.read_bytes()

    # The 14th supplied player is in, exactly once.
    assert data.count(b"L13\x00") == 1
    # The 15th and the last are nowhere at all.
    assert (b"L14\x00" in data) is False
    assert (b"L21\x00" in data) is False


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


def test_write_players_agrees_with_write_team_and_reports_a_missing_file(tmp_path):
    # The standalone entry point shares `_write_players_impl` with `write_team`,
    # so it has to report the same number; zero is reserved for "no file".
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))

    assert writer.write_players(0, _players(22)) == 14
    assert writer.write_players(31, _players(22)) == 15

    missing = tmp_path / "gone.bin"
    assert RomWriter(str(missing), str(missing)).write_players(0, _players(22)) == 0


def test_the_two_master_league_capacities_are_not_the_same_number(tmp_path):
    # Named here as well as in `test_patcher.py` because this file is where the
    # rule lives: if the two ever coincided, every count assertion above would
    # stop telling "summed the writer's answers" from "one capacity, twice".
    assert _slot_player_range(0) == (448, 14)
    assert _slot_player_range(31) == (0, 15)


# ── flag colours ─────────────────────────────────────────────────────────


def test_two_master_league_slots_have_no_known_colour_offset():
    # `_compute_ml_color_offsets` traces the upstream C++ write sequence, and
    # that sequence never writes ml[5] or ml[22]. The count and the two indices
    # are asserted separately because the comments in `rom_writer.py` state
    # both, and a table that lost a different slot would still have 30 entries.
    assert len(_ML_COLOR_OFFSETS) == 30
    assert sorted(set(range(32)) - set(_ML_COLOR_OFFSETS)) == [5, 22]


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


def test_a_slot_with_no_colour_offset_gets_the_style_byte_and_no_palette(tmp_path):
    # The consequence the `rom_writer.py` comments have to state: slots 5 and 22
    # take a new geometric flag pattern over whatever palette the original disc
    # held, and `patcher.py` counts them as patched. Inventing an offset for
    # them would write colour bytes into an unidentified ISO structure, so
    # leaving them alone is the safe behaviour and is meant to stay.
    path = _synthetic_iso(tmp_path)
    writer = RomWriter(str(path), str(path))
    team = _team()

    writer.write_flag(5, team)
    data = path.read_bytes()

    style, _ = _build_flag_data(team)
    assert [data[base + _SQUADRE_NAZ + 5] for base in _FORMA_BASES] == [style] * 5
    # The image started empty, so the five style bytes are the only thing in it:
    # 32 palette bytes would have gone somewhere, and there is nowhere for them.
    assert len(data) - data.count(b"\x00") == 5
    assert path.stat().st_size == max(_FORMA_BASES) + _SQUADRE_NAZ + 5 + 1


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
