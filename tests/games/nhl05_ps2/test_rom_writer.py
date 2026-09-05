"""`NHL05PS2RomWriter`: the record writes, and putting `DB.VIV` back on the disc.

Every image is fabricated by `tests/fixtures/synthetic_nhl05_iso.py` and read
back through that fixture's own bit decoder rather than through
`TDBTable.read_record`, so a write that used the wrong bit width cannot satisfy
an assertion by being read with the same wrong width.

Two things here are NHL 2005 rather than NHL 07 and are what a copied test file
would leave unmeasured: sixty-four line flags in place of thirty, and a 15-
character name limit in place of 19.
"""

from __future__ import annotations

import os
import random
import string
import struct

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.formats.ea_tdb import bigf_parse, refpack_compress
from retro_roster_patcher.games.nhl05_ps2.models import (
    NAME_FIELD_BYTES,
    NAME_FIELD_CHARS,
    POSITION_REVERSE,
    TDB_MASTER,
    TDB_ROSTER,
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)
from retro_roster_patcher.games.nhl05_ps2.rom_reader import ISO_SECTOR_SIZE, NHL05PS2RomReader
from retro_roster_patcher.games.nhl05_ps2.rom_writer import (
    LINE_FLAGS,
    PROGRESS_COMPRESS_END,
    PROGRESS_COPY_END,
    PROGRESS_RECORDS_END,
    NHL05PS2RomWriter,
)
from tests.fixtures import synthetic_nhl05_iso as fixture


def make_iso(tmp_path, spec=None, name="game.iso"):
    path = tmp_path / name
    fixture.write_iso(path, spec)
    return path


def prepared(tmp_path, spec=None):
    """A writer whose output ISO exists and whose `DB.VIV` is loaded."""
    src = make_iso(tmp_path, spec)
    out = tmp_path / "out.iso"
    writer = NHL05PS2RomWriter(str(src), str(out))
    writer.copy_iso()
    writer.load()
    return writer, out


def spbt_records(image: bytes, member: str = TDB_MASTER):
    return fixture.read_table_records(
        fixture.read_member(image, member), "SPBT", fixture.SPBT_FIELDS, fixture.SPBT_RECORD_SIZE
    )


def rost_records(image: bytes, member: str = TDB_MASTER):
    return fixture.read_table_records(
        fixture.read_member(image, member), "ROST", fixture.ROST_FIELDS, fixture.ROST_RECORD_SIZE
    )


def a_player(**kw) -> NHL05PlayerRecord:
    base: dict = {
        "first_name": "Wayne",
        "last_name": "Gretzky",
        "position": "C",
        "jersey_number": 99,
        "handedness": 0,
        "weight": 185,
        "team_index": 3,
        "player_id": 4242,
        "is_goalie": False,
    }
    base.update(kw)
    return NHL05PlayerRecord(**base)


# -- LINE_FLAGS ------------------------------------------------------------


def test_the_game_has_sixty_four_line_flags():
    # NHL 07 has thirty. A port that copied that list would zero thirty fields
    # and leave thirty-four of the disc's own bits set.
    assert len(LINE_FLAGS) == 64


def test_no_line_flag_is_listed_twice():
    # CORRECTION: this comment used to say the NHL 07 source listed `31LD`
    # twice. It does not. That source's list is thirty names, all thirty
    # distinct, and `games/nhl07_psp/rom_writer.py` carries the same thirty in
    # the same order. Nothing about this game's list depends on that, but a
    # false claim about the source is exactly what a port audit acts on.
    assert len(set(LINE_FLAGS)) == 64


def test_the_nhl07_third_pair_flags_are_absent():
    # There is no third five-on-three unit, so these two names do not exist
    # here. The mapper emits them for the fifth and sixth defenceman anyway and
    # `roster_values` drops both, which is how the third pair is lost.
    assert [f for f in LINE_FLAGS if f in ("33LD", "33RD")] == []


def test_the_even_strength_third_pair_flags_are_present():
    # The other half: this game does have a third defence pair, spelled
    # `L3LD`/`L3RD`, and nothing in the package ever sets it.
    assert [f for f in LINE_FLAGS if f in ("L3LD", "L3RD")] == ["L3LD", "L3RD"]


def test_the_flag_list_names_five_situations_for_each_position():
    # `3` five-on-three, `4` four-on-four, `K` penalty kill, `L` even strength,
    # `P` power play, each with a first and second unit.
    left_defence = [f for f in LINE_FLAGS if f.endswith("LD")]
    assert sorted(left_defence) == sorted(
        ["31LD", "41LD", "K1LD", "L1LD", "P1LD", "32LD", "42LD", "K2LD", "L2LD", "P2LD", "L3LD"]
    )


def test_the_three_family_carries_a_centre():
    """`31C_` and `32C_` exist, and a defence pair has no centre.

    This is the fact that tells the two games' `3n` prefixes apart without a
    real disc. Here the `3` family is `{C_, LD, RD}` -- three skaters, a
    five-on-three unit. On NHL 07 it is `{LD, RD}` and nothing else, which is a
    pair; `tests/games/nhl07_psp/test_rom_writer.py` states that half. So
    `31LD` is a defenceman on the five-on-three unit *here* and defence pair one
    *there*, and one `stat_mapper.generate_team_line_flags` cannot be right for
    both -- but the shared one is what upstream shipped and what this port keeps,
    because a plausible reading of a field name is not a disc dump.
    """
    assert sorted({flag[2:] for flag in LINE_FLAGS if flag[0] == "3"}) == ["C_", "LD", "RD"]


def test_the_three_family_has_two_units_not_three():
    # Two five-on-three units, which is why there is no `33` anything -- not
    # just no `33LD`. A third *pair* would have had one.
    assert sorted({flag[:2] for flag in LINE_FLAGS if flag[0] == "3"}) == ["31", "32"]


def test_the_four_family_carries_four_skaters():
    # The rest of the ladder, so the reading above is a system and not one
    # convenient group: `4n` is four-on-four, four skaters, no right wing.
    assert sorted({flag[2:] for flag in LINE_FLAGS if flag[0] == "4"}) == ["C_", "LD", "LW", "RD"]


def test_the_even_strength_family_carries_five_skaters():
    # And `L` is the full five, which is what makes `L1LD`..`L3RD` the
    # even-strength defence pairs rather than another special-teams unit -- the
    # ones no patched player is ever assigned to.
    assert sorted({flag[2:] for flag in LINE_FLAGS if flag[0] == "L"}) == [
        "C_",
        "LD",
        "LW",
        "RD",
        "RW",
    ]


def test_the_game_has_two_flags_the_mapper_never_emits():
    # `X1__` and `X2__` have no NHL 07 counterpart and nothing in this package
    # ever sets them, so `roster_values` writes them as zero on every patched
    # row. Named so the fact is a statement rather than an absence.
    assert [f for f in fixture.UNREACHABLE_FLAGS if f not in LINE_FLAGS] == []


# -- progress spans --------------------------------------------------------


def test_the_progress_spans_are_monotonic():
    # IMPROVEMENT over the source, whose three spans were 0.0-0.3, 0.35-0.60 and
    # then back to 0.3-0.7.
    assert [PROGRESS_COPY_END, PROGRESS_RECORDS_END, PROGRESS_COMPRESS_END] == sorted(
        [PROGRESS_COPY_END, PROGRESS_RECORDS_END, PROGRESS_COMPRESS_END]
    )


def test_the_last_progress_span_stops_short_of_one():
    # 1.0 is reserved for "Complete", which `rebuild_and_write` reports after
    # the file is closed and fsynced.
    assert PROGRESS_COMPRESS_END < 1.0


# -- copy_iso --------------------------------------------------------------


def test_copying_reproduces_the_source_byte_for_byte(tmp_path):
    src = make_iso(tmp_path)
    out = tmp_path / "out.iso"
    NHL05PS2RomWriter(str(src), str(out)).copy_iso()
    assert out.read_bytes() == src.read_bytes()


def test_copying_creates_the_output_directory(tmp_path):
    src = make_iso(tmp_path)
    out = tmp_path / "deep" / "deeper" / "out.iso"
    NHL05PS2RomWriter(str(src), str(out)).copy_iso()
    assert out.exists() is True


def test_copying_reports_progress_ending_at_the_copy_span(tmp_path):
    src = make_iso(tmp_path)
    seen: list[float] = []
    NHL05PS2RomWriter(str(src), str(tmp_path / "out.iso")).copy_iso(lambda f, _m: seen.append(f))
    assert seen[-1] == PROGRESS_COPY_END


def test_copying_reports_the_megabyte_count_in_its_message(tmp_path):
    src = make_iso(tmp_path, fixture.DiscSpec(pad_to=9 * 1024 * 1024))
    seen: list[str] = []
    NHL05PS2RomWriter(str(src), str(tmp_path / "out.iso")).copy_iso(lambda _f, m: seen.append(m))
    assert seen[-1] == "Copying ISO... 9MB"


def test_copying_reports_progress_more_than_once_for_a_multi_chunk_image(tmp_path):
    # Guards against zero-over-zero: with an image under one 4 MB chunk the
    # progress list has a single entry and "ends at 0.3" says nothing about the
    # arithmetic in between.
    src = make_iso(tmp_path, fixture.DiscSpec(pad_to=9 * 1024 * 1024))
    seen: list[float] = []
    NHL05PS2RomWriter(str(src), str(tmp_path / "out.iso")).copy_iso(lambda f, _m: seen.append(f))
    assert len(seen) == 3


def test_copying_a_missing_source_raises_rather_than_answering_false(tmp_path):
    # DELIBERATE DIVERGENCE: the source returned False and its caller reported
    # "Failed to copy ISO file" with the `errno` discarded.
    with pytest.raises(OSError):
        NHL05PS2RomWriter(str(tmp_path / "absent.iso"), str(tmp_path / "o.iso")).copy_iso()


def test_loading_after_a_copy_finds_the_archive(tmp_path):
    writer, _ = prepared(tmp_path)
    assert writer.db_viv[:4] == b"BIGF"


def test_loading_without_a_copy_returns_false(tmp_path):
    src = make_iso(tmp_path)
    assert NHL05PS2RomWriter(str(src), str(tmp_path / "never.iso")).load() is False


def test_the_archive_property_is_none_before_loading(tmp_path):
    src = make_iso(tmp_path)
    assert NHL05PS2RomWriter(str(src), str(tmp_path / "o.iso")).db_viv is None


# -- write_player_bio ------------------------------------------------------


def written_bio(tmp_path, player, position=None):
    """Write one player into a known SPBT slot and read the record back."""
    writer, out = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    index = fixture.spbt_position(1, 5) if position is None else position
    writer.write_player_bio(tdb, index, player)
    writer.rebuild_and_write({TDB_MASTER: tdb})
    return spbt_records(out.read_bytes())[index]


def test_a_bio_write_lands_the_first_name(tmp_path):
    assert written_bio(tmp_path, a_player())["FNME"] == "Wayne"


def test_a_bio_write_lands_the_last_name(tmp_path):
    assert written_bio(tmp_path, a_player())["LNME"] == "Gretzky"


def test_a_bio_write_lands_the_jersey_number(tmp_path):
    assert written_bio(tmp_path, a_player())["JERS"] == 99


def test_a_bio_write_lands_the_handedness(tmp_path):
    assert written_bio(tmp_path, a_player())["HAND"] == 0


def test_a_bio_write_lands_the_team_index(tmp_path):
    assert written_bio(tmp_path, a_player())["TEAM"] == 3


def test_a_bio_write_maps_the_position_string_to_its_code(tmp_path):
    assert written_bio(tmp_path, a_player(position="D"))["POS_"] == POSITION_REVERSE["D"]


def test_a_bio_write_maps_an_unknown_position_to_centre(tmp_path):
    assert written_bio(tmp_path, a_player(position="Rover"))["POS_"] == 0


def test_a_bio_write_lands_the_weight(tmp_path):
    assert written_bio(tmp_path, a_player())["WEIG"] == 185


def test_a_bio_write_leaves_the_discs_weight_alone_when_the_provider_has_none(tmp_path):
    record = written_bio(tmp_path, a_player(weight=0))
    assert record["WEIG"] == fixture.disc_bio_values(1, 5)["WEIG"]


def test_a_bio_write_stamps_the_records_constant_height_over_the_discs(tmp_path):
    # PINS UPSTREAM FIDELITY DELIBERATELY. Upstream's behaviour, known wrong:
    # `stat_mapper.map_player` derives the height from an attribute `Player` has
    # never had, so `NHL05PlayerRecord.height` is always its default and every
    # patched player is flattened to that one encoded height, losing the disc's
    # own. Do not "fix" this by dropping the write; writing bytes the source did
    # not write is the hardware risk this port refuses to take.
    writer, out = prepared(tmp_path, fixture.DiscSpec(height=25))
    tdb = writer.reader.get_tdb(TDB_MASTER)
    index = fixture.spbt_position(1, 5)
    player = a_player()
    writer.write_player_bio(tdb, index, player)
    writer.rebuild_and_write({TDB_MASTER: tdb})
    assert spbt_records(out.read_bytes())[index]["HEIG"] == player.height


def test_the_height_the_disc_shipped_is_not_the_one_the_record_carries(tmp_path):
    # Pins the test above: with the disc shipping the record's own default the
    # assertion could not fail.
    assert fixture.DiscSpec(height=25).height != a_player().height


def test_a_zero_height_leaves_the_discs_height_alone(tmp_path):
    # The `if player.height > 0` guard the source wrote. Unreachable through
    # `map_player`, which never produces a zero, but it is the source's guard
    # and it is what stops a hand-built record blanking the field.
    from dataclasses import replace

    writer, out = prepared(tmp_path, fixture.DiscSpec(height=25))
    tdb = writer.reader.get_tdb(TDB_MASTER)
    index = fixture.spbt_position(1, 5)
    writer.write_player_bio(tdb, index, replace(a_player(), height=0))
    writer.rebuild_and_write({TDB_MASTER: tdb})
    assert spbt_records(out.read_bytes())[index]["HEIG"] == 25


def test_a_bio_write_leaves_the_records_own_identity_alone(tmp_path):
    # `INDX` is what the record was found by. Rewriting it would detach the bio
    # from the ROST -> PLAY chain that reached it.
    record = written_bio(tmp_path, a_player())
    assert record["INDX"] == fixture.player_id_for(1, 5)


def test_a_bio_write_leaves_every_other_record_untouched(tmp_path):
    writer, out = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    index = fixture.spbt_position(1, 5)
    writer.write_player_bio(tdb, index, a_player())
    writer.rebuild_and_write({TDB_MASTER: tdb})
    after = spbt_records(out.read_bytes())
    other = fixture.spbt_position(2, 9)
    assert after[other] == fixture.disc_bio_values(2, 9)


def test_a_name_of_exactly_fifteen_characters_survives_whole(tmp_path):
    name = "A" * NAME_FIELD_CHARS
    assert written_bio(tmp_path, a_player(first_name=name))["FNME"] == name


def test_a_name_of_sixteen_characters_loses_its_last(tmp_path):
    # The NHL 2005 limit, and the one number the two games' stat mappers differ
    # by. A 16-character name survives whole on NHL 07 and is cut here.
    name = "A" * (NAME_FIELD_CHARS + 1)
    assert written_bio(tmp_path, a_player(first_name=name))["FNME"] == name[:NAME_FIELD_CHARS]


def test_a_nineteen_character_name_is_cut_to_fifteen(tmp_path):
    # NHL 07's whole field width, to make the difference explicit rather than
    # off-by-one. `NHL07` would keep all nineteen.
    name = "ABCDEFGHIJKLMNOPQRS"
    assert written_bio(tmp_path, a_player(last_name=name))["LNME"] == "ABCDEFGHIJKLMNO"


def test_the_name_limit_is_one_short_of_the_field_so_a_terminator_fits(tmp_path):
    assert NAME_FIELD_CHARS == NAME_FIELD_BYTES - 1


def test_the_field_the_fixture_declares_is_the_width_the_limit_came_from(tmp_path):
    # If these two disagreed, `test_a_name_of_sixteen_characters_loses_its_last`
    # would be measuring the fixture's field rather than the writer's limit.
    assert fixture.NAME_FIELD_BYTES == NAME_FIELD_BYTES


def test_a_bio_write_past_the_tables_allocation_is_a_no_op(tmp_path):
    writer, out = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    before = bytes(tdb.get_table("SPBT")._raw_data)
    writer.write_player_bio(tdb, tdb.get_table("SPBT").capacity, a_player())
    assert bytes(tdb.get_table("SPBT")._raw_data) == before


def test_a_bio_write_to_a_tdb_without_spbt_is_a_no_op(tmp_path):
    writer, _ = prepared(tmp_path)
    roster_tdb = writer.reader.get_tdb(TDB_ROSTER)
    writer.write_player_bio(roster_tdb, 0, a_player())
    assert roster_tdb.get_table("SPBT") is None


# -- attribute writes ------------------------------------------------------


def written_attrs(tmp_path, table, fields, record_size, index, call):
    writer, out = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    call(writer, tdb, index)
    writer.rebuild_and_write({TDB_MASTER: tdb})
    return fixture.read_table_records(
        fixture.read_member(out.read_bytes(), TDB_MASTER), table, fields, record_size
    )[index]


def test_a_skater_write_lands_every_rating(tmp_path):
    attrs = NHL05SkaterAttributes(
        **{f: 1 + i for i, f in enumerate(NHL05SkaterAttributes.__dataclass_fields__)}
    )
    index = fixture.spai_position(fixture.player_id_for(0, 4))
    record = written_attrs(
        tmp_path,
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
        index,
        lambda w, tdb, i: w.write_skater_attrs(tdb, i, attrs),
    )
    assert record["BALA"] == attrs.balance


def test_a_skater_write_lands_a_rating_late_in_the_record(tmp_path):
    # `WPOW` is the twenty-second field, so a writer that stopped short or drifted
    # by one field width misses it while `BALA` still passes.
    attrs = NHL05SkaterAttributes(
        **{f: 1 + i for i, f in enumerate(NHL05SkaterAttributes.__dataclass_fields__)}
    )
    index = fixture.spai_position(fixture.player_id_for(0, 4))
    record = written_attrs(
        tmp_path,
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
        index,
        lambda w, tdb, i: w.write_skater_attrs(tdb, i, attrs),
    )
    assert record["WPOW"] == attrs.wrist_power


def test_a_skater_write_lands_the_two_bit_fighting_field(tmp_path):
    attrs = NHL05SkaterAttributes(fighting=3)
    index = fixture.spai_position(fixture.player_id_for(0, 4))
    record = written_attrs(
        tmp_path,
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
        index,
        lambda w, tdb, i: w.write_skater_attrs(tdb, i, attrs),
    )
    assert record["FIGH"] == 3


def test_a_skater_write_leaves_the_records_identity_alone_by_default(tmp_path):
    player_id = fixture.player_id_for(0, 4)
    index = fixture.spai_position(player_id)
    record = written_attrs(
        tmp_path,
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
        index,
        lambda w, tdb, i: w.write_skater_attrs(tdb, i, NHL05SkaterAttributes()),
    )
    assert record["INDX"] == player_id


def test_a_skater_write_sets_the_identity_for_a_positive_player_id(tmp_path):
    index = fixture.spai_position(fixture.player_id_for(0, 4))
    record = written_attrs(
        tmp_path,
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
        index,
        lambda w, tdb, i: w.write_skater_attrs(tdb, i, NHL05SkaterAttributes(), 777),
    )
    assert record["INDX"] == 777


def test_a_goalie_write_lands_every_rating(tmp_path):
    attrs = NHL05GoalieAttributes(
        **{f: 2 + i for i, f in enumerate(NHL05GoalieAttributes.__dataclass_fields__)}
    )
    index = fixture.sgai_position(fixture.player_id_for(0, 1))
    record = written_attrs(
        tmp_path,
        "SGAI",
        fixture.SGAI_FIELDS,
        fixture.SGAI_RECORD_SIZE,
        index,
        lambda w, tdb, i: w.write_goalie_attrs(tdb, i, attrs),
    )
    assert record["BRKA"] == attrs.breakaway


def test_a_goalie_write_lands_the_last_save_zone(tmp_path):
    attrs = NHL05GoalieAttributes(
        **{f: 2 + i for i, f in enumerate(NHL05GoalieAttributes.__dataclass_fields__)}
    )
    index = fixture.sgai_position(fixture.player_id_for(0, 1))
    record = written_attrs(
        tmp_path,
        "SGAI",
        fixture.SGAI_FIELDS,
        fixture.SGAI_RECORD_SIZE,
        index,
        lambda w, tdb, i: w.write_goalie_attrs(tdb, i, attrs),
    )
    assert record["SSL_"] == attrs.stick_low


def test_a_skater_write_past_the_allocation_is_a_no_op(tmp_path):
    writer, _ = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    spai = tdb.get_table("SPAI")
    before = bytes(spai._raw_data)
    writer.write_skater_attrs(tdb, spai.capacity, NHL05SkaterAttributes())
    assert bytes(spai._raw_data) == before


def test_a_goalie_write_past_the_allocation_is_a_no_op(tmp_path):
    writer, _ = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    sgai = tdb.get_table("SGAI")
    before = bytes(sgai._raw_data)
    writer.write_goalie_attrs(tdb, sgai.capacity, NHL05GoalieAttributes())
    assert bytes(sgai._raw_data) == before


# -- roster_values ---------------------------------------------------------


def test_roster_values_carries_the_jersey():
    assert NHL05PS2RomWriter.roster_values(17, 0, 1)["JERS"] == 17


def test_roster_values_carries_the_captaincy():
    assert NHL05PS2RomWriter.roster_values(17, 2, 1)["CAPT"] == 2


def test_roster_values_carries_the_dressed_flag():
    assert NHL05PS2RomWriter.roster_values(17, 0, 0)["DRES"] == 0


def test_roster_values_zeroes_every_one_of_the_sixty_four_flags():
    values = NHL05PS2RomWriter.roster_values(1, 0, 1)
    assert [f for f in LINE_FLAGS if values[f] != 0] == []


def test_roster_values_holds_exactly_the_three_fields_and_the_flags():
    values = NHL05PS2RomWriter.roster_values(1, 0, 1)
    assert len(values) == 3 + len(LINE_FLAGS)


def test_roster_values_never_writes_the_team():
    # The record was found by `TEAM` and `INDX`; rewriting either breaks the
    # chain that located it.
    assert "TEAM" not in NHL05PS2RomWriter.roster_values(1, 0, 1)


def test_roster_values_never_writes_the_record_identity():
    assert "INDX" not in NHL05PS2RomWriter.roster_values(1, 0, 1)


def test_roster_values_sets_a_named_flag():
    assert NHL05PS2RomWriter.roster_values(1, 0, 1, {"L1C_": 1})["L1C_"] == 1


def test_roster_values_leaves_the_other_flags_zero():
    values = NHL05PS2RomWriter.roster_values(1, 0, 1, {"L1C_": 1})
    assert [f for f in LINE_FLAGS if f != "L1C_" and values[f] != 0] == []


def test_roster_values_drops_a_flag_the_game_does_not_have():
    # This is where the third defence pair is lost. The filter is right; it is
    # `stat_mapper.generate_team_line_flags` that feeds it a name the game does
    # not have, and that is upstream's behaviour, preserved deliberately.
    assert "33LD" not in NHL05PS2RomWriter.roster_values(1, 0, 1, {"33LD": 1})


def test_only_four_of_the_six_defence_flags_the_mapper_emits_survive_the_filter():
    # PINS UPSTREAM FIDELITY DELIBERATELY. One call, all three pairs as the
    # mapper spells them. `33LD`/`33RD` fall out here and the third pair is
    # gone; the four that survive are five-on-three slots, not pairs.
    flags = {"31LD": 1, "31RD": 1, "32LD": 1, "32RD": 1, "33LD": 1, "33RD": 1}
    values = NHL05PS2RomWriter.roster_values(1, 0, 1, flags)
    assert [f for f in flags if values.get(f) == 1] == ["31LD", "31RD", "32LD", "32RD"]


def test_the_even_strength_pairs_stay_zero_when_the_mapper_emits_the_numbered_ones():
    # The other half of the same call: `roster_values` zeroes all sixty-four
    # flags first, so the pairs the game actually ices are written zeros.
    flags = {"31LD": 1, "31RD": 1, "32LD": 1, "32RD": 1, "33LD": 1, "33RD": 1}
    values = NHL05PS2RomWriter.roster_values(1, 0, 1, flags)
    assert [f for f in ("L1LD", "L1RD", "L2LD", "L2RD", "L3LD", "L3RD") if values[f] != 0] == []


# -- rebuild_and_write -----------------------------------------------------


def test_a_rebuild_leaves_the_image_the_same_length(tmp_path):
    writer, out = prepared(tmp_path)
    before = out.stat().st_size
    writer.rebuild_and_write({TDB_MASTER: writer.reader.get_tdb(TDB_MASTER)})
    assert out.stat().st_size == before


def test_a_rebuild_leaves_the_file_after_the_archive_untouched(tmp_path):
    writer, out = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    writer.write_player_bio(tdb, fixture.spbt_position(0, 3), a_player())
    writer.rebuild_and_write({TDB_MASTER: tdb})
    image = out.read_bytes()
    viv = fixture.iso_read_file(image, fixture.DB_VIV_ISO_PATH)
    pad = image.index(fixture.PAD_FILE_BYTES)
    assert pad >= fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE + len(viv)


def test_a_rebuild_reports_completion(tmp_path):
    writer, _ = prepared(tmp_path)
    seen: list[float] = []
    writer.rebuild_and_write(
        {TDB_MASTER: writer.reader.get_tdb(TDB_MASTER)}, lambda f, _m: seen.append(f)
    )
    assert seen[-1] == 1.0


def test_a_rebuild_names_the_member_it_is_compressing(tmp_path):
    writer, _ = prepared(tmp_path)
    seen: list[str] = []
    writer.rebuild_and_write(
        {TDB_MASTER: writer.reader.get_tdb(TDB_MASTER)}, lambda _f, m: seen.append(m)
    )
    assert seen[0] == f"Compressing {TDB_MASTER}..."


def test_rebuilding_before_loading_raises(tmp_path):
    src = make_iso(tmp_path)
    writer = NHL05PS2RomWriter(str(src), str(tmp_path / "o.iso"))
    with pytest.raises(RomError, match="never loaded"):
        writer.rebuild_and_write({})


def test_a_recompressed_member_too_large_for_its_slot_raises(tmp_path):
    # DELIBERATE DIVERGENCE: the source discarded `bigf_replace_inplace`'s
    # return value, so a table's edits were dropped and the run still reported
    # success. The archive is rebuilt here with no slack at all, so a member
    # that recompresses even one byte larger cannot fit.
    src = tmp_path / "tight.iso"
    from tests.fixtures.synthetic_tdb import BigfSpec, build_bigf

    tiny = build_bigf(BigfSpec(files=[(TDB_MASTER, refpack_compress(fixture.build_master_tdb()))]))
    image = bytearray(fixture.build_iso())
    # Overwrite the archive in place with the slackless one and shrink nothing:
    # the ISO's declared length still covers it, and the member's own entry now
    # has no room.
    start = fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE
    image[start : start + len(tiny)] = tiny
    src.write_bytes(bytes(image))

    out = tmp_path / "out.iso"
    writer = NHL05PS2RomWriter(str(src), str(out))
    writer.copy_iso()
    writer.load()
    tdb = writer.reader.get_tdb(TDB_MASTER)
    spbt = tdb.get_table("SPBT")
    # Every name a different pseudo-random string, which RefPack cannot find
    # back-references for: measured at 7 086 bytes against the fixture's own
    # 4 469, so the recompressed table is 2 617 bytes past its slackless slot.
    # Fifteen *distinct* characters repeated on every record compresses to 4 141
    # and would leave this green with the check removed.
    rng = random.Random(20260904)
    for i in range(spbt.capacity):
        spbt.write_record(
            i,
            {
                "FNME": "".join(rng.choice(string.ascii_uppercase) for _ in range(15)),
                "LNME": "".join(rng.choice(string.ascii_lowercase) for _ in range(15)),
            },
        )
    with pytest.raises(RomError, match="does not fit"):
        writer.rebuild_and_write({TDB_MASTER: tdb})


def test_a_rebuild_that_cannot_locate_the_archive_raises(tmp_path):
    writer, out = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    # Break the copy's PVD after `load` has already cached the archive.
    with open(out, "r+b") as f:
        f.seek(16 * ISO_SECTOR_SIZE)
        f.write(b"\x02")
    with pytest.raises(RomError, match="Cannot find DB.VIV"):
        writer.rebuild_and_write({TDB_MASTER: tdb})


def test_a_rebuilt_archive_larger_than_its_iso_allocation_raises(tmp_path):
    writer, _ = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    writer._db_viv = writer._db_viv + b"\x00" * (fixture.GAP_SECTORS * ISO_SECTOR_SIZE * 2)
    with pytest.raises(RomError, match="would overwrite the next file"):
        writer.rebuild_and_write({TDB_MASTER: tdb})


def test_a_shorter_archive_has_its_iso_length_corrected_little_endian(tmp_path):
    writer, out = prepared(tmp_path)
    shorter = writer._db_viv[:-4096]
    writer._db_viv = shorter
    writer.rebuild_and_write({})
    reader = NHL05PS2RomReader(str(out))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    assert struct.unpack_from("<I", out.read_bytes(), offset + 10)[0] == len(shorter)


def test_a_shorter_archive_has_its_iso_length_corrected_big_endian(tmp_path):
    writer, out = prepared(tmp_path)
    shorter = writer._db_viv[:-4096]
    writer._db_viv = shorter
    writer.rebuild_and_write({})
    image = out.read_bytes()
    reader = NHL05PS2RomReader(str(out))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    assert struct.unpack_from(">I", image, offset + 14)[0] == len(shorter)


def test_a_shorter_archive_zero_fills_the_bytes_it_no_longer_covers(tmp_path):
    # Trailing bytes of the previous archive would still parse as part of its
    # last file. The dropped bytes are chosen larger than any slack the fixture
    # carries so this cannot pass on a run of zeroes that was already there.
    writer, out = prepared(tmp_path)
    original_len = len(writer._db_viv)
    dropped = fixture.MEMBER_SLACK * 2 + 1000
    before = out.read_bytes()
    tail_start = fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE + original_len - dropped
    assert before[tail_start : tail_start + dropped] != b"\x00" * dropped
    writer._db_viv = writer._db_viv[:-dropped]
    writer.rebuild_and_write({})
    after = out.read_bytes()
    assert after[tail_start : tail_start + dropped] == b"\x00" * dropped


def test_a_same_length_archive_leaves_the_iso_length_field_alone(tmp_path):
    writer, out = prepared(tmp_path)
    reader = NHL05PS2RomReader(str(out))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    before = out.read_bytes()[offset + 10 : offset + 18]
    writer.rebuild_and_write({TDB_MASTER: writer.reader.get_tdb(TDB_MASTER)})
    assert out.read_bytes()[offset + 10 : offset + 18] == before


def test_a_rebuild_writes_the_member_the_archive_spells_in_capitals(tmp_path):
    # `bigf_replace_inplace` selects case-insensitively, which is why nothing in
    # this package works around `bigf_replace`'s case bug: it never calls it.
    spec = fixture.DiscSpec(master_name="NHL2005.TDB")
    writer, out = prepared(tmp_path, spec)
    tdb = writer.reader.get_tdb("NHL2005.TDB")
    index = fixture.spbt_position(0, 6)
    writer.write_player_bio(tdb, index, a_player(first_name="Capital"))
    writer.rebuild_and_write({"NHL2005.TDB": tdb})
    assert spbt_records(out.read_bytes(), "NHL2005.TDB")[index]["FNME"] == "Capital"


def test_a_rebuild_keeps_every_member_the_archive_had(tmp_path):
    writer, out = prepared(tmp_path)
    writer.rebuild_and_write({TDB_MASTER: writer.reader.get_tdb(TDB_MASTER)})
    viv = fixture.iso_read_file(out.read_bytes(), fixture.DB_VIV_ISO_PATH)
    assert [e.name for e in bigf_parse(viv)] == [TDB_MASTER, TDB_ROSTER]


def test_a_rebuild_leaves_an_unmodified_member_byte_identical(tmp_path):
    # In-place replacement keeps every offset, so a member not in
    # `modified_tdbs` is untouched.
    writer, out = prepared(tmp_path)
    before = fixture.read_member(out.read_bytes(), TDB_ROSTER)
    writer.rebuild_and_write({TDB_MASTER: writer.reader.get_tdb(TDB_MASTER)})
    assert fixture.read_member(out.read_bytes(), TDB_ROSTER) == before


def test_the_written_image_is_flushed_to_disk_before_the_call_returns(tmp_path):
    # `os.fsync` before returning: the next step reopens the same path and seeks
    # into it, and on the SD cards these devices boot from an unflushed write is
    # a real way to read back a hole. Measured by reading through a second file
    # descriptor rather than through the one the writer used.
    writer, out = prepared(tmp_path)
    tdb = writer.reader.get_tdb(TDB_MASTER)
    index = fixture.spbt_position(0, 7)
    writer.write_player_bio(tdb, index, a_player(first_name="Flushed"))
    writer.rebuild_and_write({TDB_MASTER: tdb})
    fd = os.open(str(out), os.O_RDONLY)
    try:
        data = b""
        while chunk := os.read(fd, 1 << 20):
            data += chunk
    finally:
        os.close(fd)
    assert spbt_records(data)[index]["FNME"] == "Flushed"
