"""`NHL07PSPRomWriter`: record writes, the archive rebuild, the write-back.

Every value written here is read back with the fixture's own `unpack_bits`,
against the fixture's own `FieldSpec` list -- **not** with
`TDBTable.read_record`. A test that wrote through the module and read back
through the module agrees with itself whatever bit width both used, and this
game's whole correctness lives in bit widths and field names.

Where a record's contents are asserted, the assertion names the value the
fixture disc shipped as well as the value written, so "the field changed" is not
satisfied by a field that was already right.
"""

from __future__ import annotations

import os
import struct

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.formats.ea_tdb import TDBFile
from retro_roster_patcher.games.nhl07_psp.models import (
    NAME_FIELD_CHARS,
    POSITION_REVERSE,
    TDB_BIOATT,
    TDB_MASTER,
    TDB_ROSTER,
    NHL07GoalieAttributes,
    NHL07PlayerRecord,
    NHL07SkaterAttributes,
)
from retro_roster_patcher.games.nhl07_psp.rom_reader import ISO_SECTOR_SIZE, NHL07PSPRomReader
from retro_roster_patcher.games.nhl07_psp.rom_writer import (
    LINE_FLAGS,
    PROGRESS_COPY_END,
    PROGRESS_RECORDS_END,
    NHL07PSPRomWriter,
)
from tests.fixtures import synthetic_nhl07_iso as fixture


def prepared(tmp_path, spec=None):
    """A writer with the ISO copied and `db.viv` loaded, ready to edit."""
    source = tmp_path / "source.iso"
    fixture.write_iso(source, spec)
    writer = NHL07PSPRomWriter(str(source), str(tmp_path / "out.iso"))
    writer.copy_iso()
    writer.load()
    return writer


def master(writer) -> TDBFile:
    return writer.reader.get_tdb(TDB_MASTER)


def read_back(path, member, table, fields, record_size, index):
    """One record of a patched image, decoded independently of the module."""
    records = fixture.read_table_records(
        fixture.read_member(path.read_bytes(), member), table, fields, record_size
    )
    return records[index]


SKATER = NHL07PlayerRecord(
    first_name="Aleksander",
    last_name="Barkov-Junior",
    position="C",
    jersey_number=16,
    handedness=0,
    weight=213,
    team_index=12,
    player_id=5000,
    is_goalie=False,
    skater_attrs=NHL07SkaterAttributes(
        balance=1,
        penalty=2,
        shot_accuracy=3,
        wrist_accuracy=4,
        faceoffs=5,
        acceleration=6,
        speed=7,
        potential=8,
        deking=9,
        checking=10,
        toughness=11,
        fighting=3,
        puck_control=12,
        agility=13,
        hero=14,
        aggression=15,
        pressure=16,
        passing=17,
        endurance=18,
        injury=19,
        slap_power=20,
        wrist_power=21,
    ),
)

GOALIE = NHL07PlayerRecord(
    first_name="Sergei",
    last_name="Bobrovsky",
    position="G",
    jersey_number=72,
    handedness=1,
    weight=187,
    team_index=12,
    player_id=5001,
    is_goalie=True,
    goalie_attrs=NHL07GoalieAttributes(
        breakaway=1,
        rebound_ctrl=2,
        shot_recovery=3,
        speed=4,
        poke_check=5,
        intensity=6,
        potential=7,
        toughness=8,
        fighting=2,
        agility=9,
        five_hole=10,
        passing=11,
        endurance=12,
        glove_high=13,
        stick_high=14,
        glove_low=15,
        stick_low=16,
    ),
)


def test_there_are_thirty_line_flags():
    assert len(LINE_FLAGS) == 30


def test_no_line_flag_is_listed_twice():
    # `roster_values` zeroes each of them and then sets the ones a player has,
    # so a duplicate would be harmless -- but it would also mean the list is not
    # what its comment claims, and the count above would stop meaning anything.
    assert sorted(set(LINE_FLAGS)) == sorted(LINE_FLAGS)


def test_every_line_flag_is_exactly_four_characters():
    assert [f for f in LINE_FLAGS if len(f) != 4] == []


def test_the_defence_pairs_are_numbered_not_lettered():
    # `31LD` and not `L1LD`, which is the TDB's own spelling and reads like a
    # transcription slip until you check it.
    assert [f for f in LINE_FLAGS if f.endswith(("LD", "RD"))] == [
        "31LD",
        "32LD",
        "33LD",
        "31RD",
        "32RD",
        "33RD",
    ]


def test_the_list_is_the_sources_thirty_names_in_the_sources_order():
    """Transcribed from `nhl07_psp_patcher/rom_writer.py`, not from the port.

    The one assertion here that fails if a future port audit "restores" an
    `L1LD` to this game. There is nothing to restore: the source's list is these
    thirty and no others, and `L1LD` appears exactly once in the whole source
    tree, in a *different* game's writer. Written out in full rather than
    derived, because every derivation above this line is satisfied by more than
    one list and a reordering would pass all of them.
    """
    assert LINE_FLAGS == [
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


def test_no_flag_uses_nhl05s_lettered_defence_spelling():
    assert [f for f in LINE_FLAGS if f in ("L1LD", "L2LD", "L3LD", "L1RD", "L2RD", "L3RD")] == []


def test_every_numbered_flag_is_a_defence_slot():
    """The evidence that `3n` is a defence *pair* on this game and not a unit.

    Group the list by prefix and read off which position suffixes each prefix
    carries. Here the numbered family carries `LD` and `RD` and nothing else --
    a pair. NHL 05's numbered family carries `31C_` as well, which is what makes
    it a three-skater strength unit there; see
    `tests/games/nhl05_ps2/test_rom_writer.py`. Same prefix, two games, two
    meanings, which is why one mapper cannot serve both.
    """
    assert sorted({flag[2:] for flag in LINE_FLAGS if flag[0].isdigit()}) == ["LD", "RD"]


def test_there_are_three_numbered_groups_and_no_more():
    # Three pairs, because a dressed NHL side ices three. If `3n` were a
    # strength unit there would be no even-strength defence flag in this list at
    # all and the game could not pair a defenceman.
    assert sorted({flag[:2] for flag in LINE_FLAGS if flag[0].isdigit()}) == ["31", "32", "33"]


def test_the_progress_spans_are_ordered():
    assert PROGRESS_COPY_END < PROGRESS_RECORDS_END


def test_the_copy_is_byte_identical_to_the_source(tmp_path):
    source = tmp_path / "source.iso"
    fixture.write_iso(source)
    out = tmp_path / "out.iso"
    NHL07PSPRomWriter(str(source), str(out)).copy_iso()
    assert out.read_bytes() == source.read_bytes()


def test_the_copy_is_not_empty(tmp_path):
    # Pins the assertion above: two empty files are byte-identical too.
    source = tmp_path / "source.iso"
    fixture.write_iso(source)
    out = tmp_path / "out.iso"
    NHL07PSPRomWriter(str(source), str(out)).copy_iso()
    assert out.stat().st_size > fixture.ISO_SECTOR_SIZE * 20


def test_copying_creates_the_output_directory(tmp_path):
    source = tmp_path / "source.iso"
    fixture.write_iso(source)
    out = tmp_path / "deep" / "nested" / "out.iso"
    NHL07PSPRomWriter(str(source), str(out)).copy_iso()
    assert out.exists() is True


def test_copying_reports_progress_ending_at_the_copy_span(tmp_path):
    source = tmp_path / "source.iso"
    fixture.write_iso(source, fixture.DiscSpec(pad_to=9 * 1024 * 1024))
    seen: list[float] = []
    NHL07PSPRomWriter(str(source), str(tmp_path / "out.iso")).copy_iso(
        lambda pct, msg: seen.append(pct)
    )
    assert seen[-1] == PROGRESS_COPY_END


def test_copying_a_multi_chunk_image_reports_more_than_one_step(tmp_path):
    # 9 MB in 4 MB chunks is three reports. Without a padded image the whole
    # file is one chunk and the assertion above would hold for a writer that
    # reported once, at the end.
    source = tmp_path / "source.iso"
    fixture.write_iso(source, fixture.DiscSpec(pad_to=9 * 1024 * 1024))
    seen: list[float] = []
    NHL07PSPRomWriter(str(source), str(tmp_path / "out.iso")).copy_iso(
        lambda pct, msg: seen.append(pct)
    )
    assert len(seen) == 3


def test_copying_reports_monotonically(tmp_path):
    source = tmp_path / "source.iso"
    fixture.write_iso(source, fixture.DiscSpec(pad_to=9 * 1024 * 1024))
    seen: list[float] = []
    NHL07PSPRomWriter(str(source), str(tmp_path / "out.iso")).copy_iso(
        lambda pct, msg: seen.append(pct)
    )
    assert seen == sorted(seen)


def test_copying_a_missing_source_raises_oserror(tmp_path):
    # DELIBERATE DIVERGENCE: the source returned `False` with the `errno`
    # discarded, so a full card, a read-only mount and a vanished file all read
    # the same. `patch` wraps this in `errors.as_rom_error`.
    writer = NHL07PSPRomWriter(str(tmp_path / "absent.iso"), str(tmp_path / "out.iso"))
    with pytest.raises(OSError):
        writer.copy_iso()


def test_loading_after_copying_finds_the_archive(tmp_path):
    assert prepared(tmp_path).db_viv is not None


def test_loading_a_copy_with_no_archive_answers_false(tmp_path):
    source = tmp_path / "source.iso"
    fixture.write_iso(source, fixture.DiscSpec(db_dir_name="XX"))
    writer = NHL07PSPRomWriter(str(source), str(tmp_path / "out.iso"))
    writer.copy_iso()
    assert writer.load() is False


def test_the_writer_reads_the_output_and_not_the_input(tmp_path):
    # `copy_iso` writes the output and `load` opens the output, so every
    # subsequent read is of the file the user will keep. Asserting against the
    # input would assert nothing about what was written.
    writer = prepared(tmp_path)
    assert writer.reader.iso_path == writer.output_path


def test_a_bio_write_lands_the_first_name_in_the_named_field(tmp_path):
    writer = prepared(tmp_path)
    writer.write_player_bio(master(writer), 3, SKATER)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    record = read_back(
        writer.output_path and __import__("pathlib").Path(writer.output_path),
        TDB_MASTER,
        "SPBT",
        fixture.SPBT_FIELDS,
        fixture.SPBT_RECORD_SIZE,
        3,
    )
    assert record["FNME"] == "Aleksander"


def _write_and_read(tmp_path, index, player, table="SPBT"):
    from pathlib import Path

    writer = prepared(tmp_path)
    writer.write_player_bio(master(writer), index, player)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    return read_back(
        Path(writer.output_path),
        TDB_MASTER,
        table,
        fixture.SPBT_FIELDS,
        fixture.SPBT_RECORD_SIZE,
        index,
    )


def test_a_bio_write_lands_the_last_name(tmp_path):
    assert _write_and_read(tmp_path, 3, SKATER)["LNME"] == "Barkov-Junior"


def test_a_bio_write_lands_the_jersey_number(tmp_path):
    # The disc ships jerseys from 90 upwards, so 16 cannot be the value that was
    # already there.
    assert _write_and_read(tmp_path, 3, SKATER)["JERS"] == 16


def test_the_jersey_the_disc_shipped_is_not_the_one_written(tmp_path):
    team, row = divmod(
        (3 * fixture.SPBT_STRIDE + fixture.SPBT_SHIFT) % fixture.PLAYER_COUNT, fixture.ROWS_PER_TEAM
    )
    assert fixture.disc_bio_values(team, row)["JERS"] != SKATER.jersey_number


def test_a_bio_write_lands_the_handedness_bit(tmp_path):
    assert _write_and_read(tmp_path, 3, SKATER)["HAND"] == 0


def test_a_bio_write_lands_the_team_index(tmp_path):
    assert _write_and_read(tmp_path, 3, SKATER)["TEAM"] == 12


def test_a_bio_write_lands_the_position_code(tmp_path):
    assert _write_and_read(tmp_path, 3, SKATER)["POS_"] == POSITION_REVERSE["C"]


def test_a_goalie_bio_write_lands_the_goalie_position_code(tmp_path):
    # `POS_` is three bits and `G` is 4, so this is the one position code whose
    # top bit is set -- the case a two-bit write would lose.
    assert _write_and_read(tmp_path, 5, GOALIE)["POS_"] == 4


def test_a_bio_write_lands_the_weight(tmp_path):
    assert _write_and_read(tmp_path, 3, SKATER)["WEIG"] == 213


def test_a_bio_write_leaves_the_records_index_alone(tmp_path):
    # `INDX` is how the record was found. Rewriting it would detach the bio from
    # the attributes and the roster row that point at it.
    flat = (3 * fixture.SPBT_STRIDE + fixture.SPBT_SHIFT) % fixture.PLAYER_COUNT
    team, row = divmod(flat, fixture.ROWS_PER_TEAM)
    assert _write_and_read(tmp_path, 3, SKATER)["INDX"] == fixture.player_id_for(team, row)


def test_a_bio_write_stamps_the_records_constant_height_over_the_discs(tmp_path):
    # PINS UPSTREAM FIDELITY DELIBERATELY. This is upstream's behaviour and it
    # is known wrong: `stat_mapper.map_player` derives the height from
    # `getattr(player, "height", 0)` against a `Player` with no `height`, so
    # `NHL07PlayerRecord.height` is always its default and every patched player
    # is flattened to that one encoded height. The disc's own per-player value
    # is lost. Do not "fix" this by dropping the write -- writing a byte
    # sequence the source did not write is a hardware risk this project will not
    # take on output that has never been checked against a retail UMD.
    from pathlib import Path

    writer = prepared(tmp_path, fixture.DiscSpec(height=29))
    writer.write_player_bio(master(writer), 3, SKATER)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    record = read_back(
        Path(writer.output_path),
        TDB_MASTER,
        "SPBT",
        fixture.SPBT_FIELDS,
        fixture.SPBT_RECORD_SIZE,
        3,
    )
    assert record["HEIG"] == SKATER.height


def test_the_height_the_disc_shipped_is_not_the_one_the_record_carries(tmp_path):
    # Pins the test above. With the disc shipping the record's own default,
    # "stamped the record's constant" and "left the disc alone" are the same
    # bytes and the assertion proves nothing.
    assert fixture.DiscSpec(height=29).height != SKATER.height


def test_a_zero_weight_leaves_the_discs_weight_alone(tmp_path):
    from dataclasses import replace

    flat = (3 * fixture.SPBT_STRIDE + fixture.SPBT_SHIFT) % fixture.PLAYER_COUNT
    team, row = divmod(flat, fixture.ROWS_PER_TEAM)
    disc_weight = fixture.disc_bio_values(team, row)["WEIG"]
    written = _write_and_read(tmp_path, 3, replace(SKATER, weight=0))
    assert written["WEIG"] == disc_weight


def test_a_zero_height_leaves_the_discs_height_alone(tmp_path):
    # The `if player.height > 0` guard the source wrote. Unreachable through
    # `map_player`, which never produces a zero, but it is the source's guard
    # and it is what stops a hand-built record blanking the field.
    from dataclasses import replace
    from pathlib import Path

    writer = prepared(tmp_path, fixture.DiscSpec(height=29))
    writer.write_player_bio(master(writer), 3, replace(SKATER, height=0))
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    record = read_back(
        Path(writer.output_path),
        TDB_MASTER,
        "SPBT",
        fixture.SPBT_FIELDS,
        fixture.SPBT_RECORD_SIZE,
        3,
    )
    assert record["HEIG"] == 29


def test_a_zero_weight_still_writes_the_rest_of_the_record(tmp_path):
    from dataclasses import replace

    assert _write_and_read(tmp_path, 3, replace(SKATER, weight=0))["FNME"] == "Aleksander"


def test_a_name_longer_than_the_field_is_truncated(tmp_path):
    from dataclasses import replace

    long_name = "Abcdefghijklmnopqrstuvwxyz"
    written = _write_and_read(tmp_path, 3, replace(SKATER, first_name=long_name))
    assert written["FNME"] == long_name[:NAME_FIELD_CHARS]


def test_a_truncated_name_leaves_a_terminator(tmp_path):
    # The field is 20 bytes and the writer keeps 19, so the twentieth stays NUL
    # and the game can find the end of the string.
    from dataclasses import replace

    assert len(_write_and_read(tmp_path, 3, replace(SKATER, first_name="A" * 40))["FNME"]) == 19


def test_a_bio_write_to_a_tdb_without_the_table_is_a_no_op(tmp_path):
    writer = prepared(tmp_path)
    roster_tdb = writer.reader.get_tdb(TDB_ROSTER)
    writer.write_player_bio(roster_tdb, 0, SKATER)
    assert roster_tdb.get_table("SPBT") is None


def test_a_bio_write_past_the_allocation_is_a_no_op(tmp_path):
    # Rather than the `IndexError` `TDBTable.write_record` would raise. The
    # mirror TDB need not have the master's capacity, and this is what lets the
    # patcher write to both without checking each.
    writer = prepared(tmp_path)
    spbt = master(writer).get_table("SPBT")
    before = bytes(spbt._raw_data)
    writer.write_player_bio(master(writer), spbt.capacity, SKATER)
    assert bytes(spbt._raw_data) == before


def _write_attrs_and_read(tmp_path, index, kind):
    from pathlib import Path

    writer = prepared(tmp_path)
    if kind == "skater":
        writer.write_skater_attrs(master(writer), index, SKATER.skater_attrs)
        table, fields, size = "SPAI", fixture.SPAI_FIELDS, fixture.SPAI_RECORD_SIZE
    else:
        writer.write_goalie_attrs(master(writer), index, GOALIE.goalie_attrs)
        table, fields, size = "SGAI", fixture.SGAI_FIELDS, fixture.SGAI_RECORD_SIZE
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    return read_back(Path(writer.output_path), TDB_MASTER, table, fields, size, index)


SKATER_EXPECTED = {
    "BALA": 1,
    "PENA": 2,
    "SACC": 3,
    "WACC": 4,
    "FACE": 5,
    "ACCE": 6,
    "SPEE": 7,
    "POTE": 8,
    "DEKG": 9,
    "CHKG": 10,
    "TOUG": 11,
    "PUCK": 12,
    "AGIL": 13,
    "HERO": 14,
    "AGGR": 15,
    "PRES": 16,
    "PASS": 17,
    "ENDU": 18,
    "INJU": 19,
    "SPOW": 20,
    "WPOW": 21,
    "FIGH": 3,
}


@pytest.mark.parametrize("field,value", sorted(SKATER_EXPECTED.items()))
def test_each_skater_rating_lands_in_its_own_field(tmp_path, field, value):
    # Every rating is a different number, so a writer that put one field's value
    # into the next field's bits lands on a number that belongs elsewhere in the
    # record and fails here.
    assert _write_attrs_and_read(tmp_path, 4, "skater")[field] == value


def test_the_skater_ratings_are_all_distinct():
    # Pins the parametrisation above as meaningful: with two equal ratings, a
    # swap between them would be invisible.
    assert len(set(SKATER_EXPECTED.values())) == len(SKATER_EXPECTED) - 1


GOALIE_EXPECTED = {
    "BRKA": 1,
    "REBC": 2,
    "SREC": 3,
    "SPEE": 4,
    "POKE": 5,
    "INTE": 6,
    "POTE": 7,
    "TOUG": 8,
    "AGIL": 9,
    "5HOL": 10,
    "PASS": 11,
    "ENDU": 12,
    "GSH_": 13,
    "SSH_": 14,
    "GSL_": 15,
    "SSL_": 16,
    "FIGH": 2,
}


@pytest.mark.parametrize("field,value", sorted(GOALIE_EXPECTED.items()))
def test_each_goalie_rating_lands_in_its_own_field(tmp_path, field, value):
    assert _write_attrs_and_read(tmp_path, 2, "goalie")[field] == value


def test_a_skater_write_does_not_change_the_records_index(tmp_path):
    ids = list(
        reversed(
            [
                fixture.player_id_for(t, r)
                for t in range(fixture.TEAM_COUNT)
                for r in range(fixture.ROWS_PER_TEAM)
                if not fixture.is_goalie_row(r)
            ]
        )
    )
    assert _write_attrs_and_read(tmp_path, 4, "skater")["INDX"] == ids[4]


def test_a_skater_write_with_a_player_id_sets_the_index(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    writer.write_skater_attrs(master(writer), 4, SKATER.skater_attrs, player_id=1234)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    record = read_back(
        Path(writer.output_path),
        TDB_MASTER,
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
        4,
    )
    assert record["INDX"] == 1234


def test_a_goalie_write_with_a_player_id_sets_the_index(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    writer.write_goalie_attrs(master(writer), 2, GOALIE.goalie_attrs, player_id=4321)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    record = read_back(
        Path(writer.output_path),
        TDB_MASTER,
        "SGAI",
        fixture.SGAI_FIELDS,
        fixture.SGAI_RECORD_SIZE,
        2,
    )
    assert record["INDX"] == 4321


def test_a_rating_over_the_field_width_saturates_rather_than_wrapping(tmp_path):
    # `_write_bits` clamps into `0 .. 2**width - 1`. A six-bit field handed 200
    # must read back 63 and not 200 & 63 == 8, which is what a wrap would give.
    from dataclasses import replace
    from pathlib import Path

    writer = prepared(tmp_path)
    writer.write_skater_attrs(master(writer), 4, replace(SKATER.skater_attrs, balance=200))
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    record = read_back(
        Path(writer.output_path),
        TDB_MASTER,
        "SPAI",
        fixture.SPAI_FIELDS,
        fixture.SPAI_RECORD_SIZE,
        4,
    )
    assert record["BALA"] == 63


def test_an_attribute_write_to_a_tdb_without_the_table_is_a_no_op(tmp_path):
    writer = prepared(tmp_path)
    roster_tdb = writer.reader.get_tdb(TDB_ROSTER)
    writer.write_skater_attrs(roster_tdb, 0, SKATER.skater_attrs)
    assert roster_tdb.get_table("SPAI") is None


def test_an_attribute_write_past_the_allocation_is_a_no_op(tmp_path):
    writer = prepared(tmp_path)
    spai = master(writer).get_table("SPAI")
    before = bytes(spai._raw_data)
    writer.write_skater_attrs(master(writer), spai.capacity, SKATER.skater_attrs)
    assert bytes(spai._raw_data) == before


def test_roster_values_carries_the_jersey():
    assert NHL07PSPRomWriter.roster_values(44, 0, 1)["JERS"] == 44


def test_roster_values_carries_the_captaincy():
    assert NHL07PSPRomWriter.roster_values(44, 2, 1)["CAPT"] == 2


def test_roster_values_carries_the_dressed_level():
    assert NHL07PSPRomWriter.roster_values(44, 0, 0)["DRES"] == 0


def test_roster_values_zeroes_every_line_flag_by_default():
    values = NHL07PSPRomWriter.roster_values(1, 0, 1)
    assert [f for f in LINE_FLAGS if values[f] != 0] == []


def test_roster_values_names_every_line_flag():
    # A record is reused, not created, so a flag left out of the mapping keeps
    # whatever its previous occupant had.
    values = NHL07PSPRomWriter.roster_values(1, 0, 1)
    assert sorted(f for f in values if f in LINE_FLAGS) == sorted(LINE_FLAGS)


def test_roster_values_sets_only_the_flags_it_was_given():
    values = NHL07PSPRomWriter.roster_values(1, 0, 1, {"L2LW": 1, "H3__": 1})
    assert sorted(f for f in LINE_FLAGS if values[f] == 1) == ["H3__", "L2LW"]


def test_roster_values_drops_a_flag_name_it_does_not_know():
    values = NHL07PSPRomWriter.roster_values(1, 0, 1, {"ZZZZ": 1})
    assert "ZZZZ" not in values


def test_roster_values_does_not_write_the_team():
    # The record was found by `TEAM`, and rewriting it would move the row to
    # another team.
    assert "TEAM" not in NHL07PSPRomWriter.roster_values(1, 0, 1)


def test_roster_values_does_not_write_the_index():
    # Rewriting `INDX` breaks the ROST -> PLAY -> SPBT chain that located it.
    assert "INDX" not in NHL07PSPRomWriter.roster_values(1, 0, 1)


def test_a_roster_write_clears_the_flag_the_disc_had_set(tmp_path):
    # The fixture ships one flag set per row. This is what would be left behind
    # by a writer that only set the flags a player has.
    from pathlib import Path

    writer = prepared(tmp_path)
    rost = master(writer).get_table("ROST")
    rost.write_record(7, NHL07PSPRomWriter.roster_values(9, 0, 1, {"L1C_": 1}))
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    record = read_back(
        Path(writer.output_path),
        TDB_MASTER,
        "ROST",
        fixture.ROST_FIELDS,
        fixture.ROST_RECORD_SIZE,
        7,
    )
    assert [f for f in fixture.LINE_FLAG_NAMES if record[f] == 1] == ["L1C_"]


def test_the_disc_shipped_that_row_with_a_different_flag_set(tmp_path):
    # Pins the test above: without a flag already set, "cleared it" is vacuous.
    records = fixture.read_table_records(
        fixture.build_master_tdb(), "ROST", fixture.ROST_FIELDS, fixture.ROST_RECORD_SIZE
    )
    assert [f for f in fixture.LINE_FLAG_NAMES if records[7][f] == 1] == ["L4LW"]


def test_rebuilding_leaves_the_image_the_same_length(tmp_path):
    # In-place replacement inside `db.viv`, so nothing on the disc moves.
    from pathlib import Path

    writer = prepared(tmp_path)
    before = Path(writer.output_path).stat().st_size
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    assert Path(writer.output_path).stat().st_size == before


def test_rebuilding_leaves_the_file_after_the_archive_untouched(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    writer.write_player_bio(master(writer), 3, SKATER)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    image = Path(writer.output_path).read_bytes()
    assert fixture.iso_read_file(image, "PSP_GAME/USRDIR/DB/ZZPAD.BIN") == fixture.PAD_FILE_BYTES


def test_rebuilding_changes_the_image(tmp_path):
    # Pins every "unchanged" assertion above: a rebuild that wrote nothing at
    # all would satisfy them.
    from pathlib import Path

    source = tmp_path / "source.iso"
    fixture.write_iso(source)
    writer = NHL07PSPRomWriter(str(source), str(tmp_path / "out.iso"))
    writer.copy_iso()
    writer.load()
    writer.write_player_bio(master(writer), 3, SKATER)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    assert Path(writer.output_path).read_bytes() != source.read_bytes()


def test_rebuilding_keeps_the_archive_at_its_original_sector(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    reader = NHL07PSPRomReader(str(Path(writer.output_path)))
    reader.load()
    assert reader.find_db_viv_location()[0] == fixture.DB_VIV_SECTOR


def test_rebuilding_writes_the_archive_at_its_logical_block_address(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    image = Path(writer.output_path).read_bytes()
    offset = fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE
    assert image[offset : offset + 4] == b"BIGF"


def test_rebuilding_reports_progress_ending_at_one(tmp_path):
    writer = prepared(tmp_path)
    seen: list[float] = []
    writer.rebuild_and_write({TDB_MASTER: master(writer)}, lambda pct, msg: seen.append(pct))
    assert seen[-1] == 1.0


def test_rebuilding_reports_progress_starting_at_the_records_span(tmp_path):
    writer = prepared(tmp_path)
    seen: list[float] = []
    writer.rebuild_and_write({TDB_MASTER: master(writer)}, lambda pct, msg: seen.append(pct))
    assert seen[0] == PROGRESS_RECORDS_END


def test_rebuilding_reports_progress_monotonically(tmp_path):
    # IMPROVEMENT: the source's compression span restarted at 0.3 after the
    # record-writing span had reached 0.6, so a progress bar ran backwards.
    writer = prepared(tmp_path)
    seen: list[float] = []
    writer.rebuild_and_write(
        {
            TDB_MASTER: master(writer),
            TDB_BIOATT: writer.reader.get_tdb(TDB_BIOATT),
            TDB_ROSTER: writer.reader.get_tdb(TDB_ROSTER),
        },
        lambda pct, msg: seen.append(pct),
    )
    assert seen == sorted(seen)


def test_rebuilding_three_tdbs_reports_a_step_for_each(tmp_path):
    writer = prepared(tmp_path)
    seen: list[str] = []
    writer.rebuild_and_write(
        {
            TDB_MASTER: master(writer),
            TDB_BIOATT: writer.reader.get_tdb(TDB_BIOATT),
            TDB_ROSTER: writer.reader.get_tdb(TDB_ROSTER),
        },
        lambda pct, msg: seen.append(msg),
    )
    assert [m for m in seen if m.startswith("Compressing")] == [
        f"Compressing {TDB_MASTER}...",
        f"Compressing {TDB_BIOATT}...",
        f"Compressing {TDB_ROSTER}...",
    ]


def test_rebuilding_before_loading_raises(tmp_path):
    source = tmp_path / "source.iso"
    fixture.write_iso(source)
    writer = NHL07PSPRomWriter(str(source), str(tmp_path / "out.iso"))
    with pytest.raises(RomError):
        writer.rebuild_and_write({})


def test_a_tdb_too_large_for_its_slot_raises(tmp_path):
    # DELIBERATE DIVERGENCE. The source discarded `bigf_replace_inplace`'s
    # return value, under a comment reasoning that a split TDB could be skipped
    # because the master has every table. The effect was a disc written back
    # with two of its three TDBs disagreeing about the same roster, reported as
    # a success.
    #
    # The slot is shrunk to 8 bytes, which no RefPack stream fits.
    writer = prepared(tmp_path)
    viv = bytearray(writer.db_viv)
    from retro_roster_patcher.formats.ea_tdb import bigf_parse

    entries = bigf_parse(bytes(viv))
    target = next(e for e in entries if e.name == TDB_MASTER)
    position = viv.find(TDB_MASTER.encode("ascii")) - 4
    struct.pack_into(">I", viv, position, 8)
    writer._db_viv = bytes(viv)
    assert target.size > 8
    with pytest.raises(RomError):
        writer.rebuild_and_write({TDB_MASTER: master(writer)})


def test_the_message_for_an_over_large_tdb_names_the_file(tmp_path):
    writer = prepared(tmp_path)
    viv = bytearray(writer.db_viv)
    position = viv.find(TDB_MASTER.encode("ascii")) - 4
    struct.pack_into(">I", viv, position, 8)
    writer._db_viv = bytes(viv)
    with pytest.raises(RomError, match=TDB_MASTER):
        writer.rebuild_and_write({TDB_MASTER: master(writer)})


def test_a_member_the_archive_does_not_hold_raises(tmp_path):
    writer = prepared(tmp_path)
    with pytest.raises(RomError, match="nhl2099.tdb"):
        writer.rebuild_and_write({"nhl2099.tdb": master(writer)})


def test_an_archive_larger_than_its_iso_allocation_raises(tmp_path):
    # The second of the two size checks: the first bounds a `.tdb` inside
    # `db.viv`, this one bounds `db.viv` inside the sector gap before the next
    # file on the disc.
    writer = prepared(tmp_path)
    writer._db_viv = bytes(writer.db_viv) + b"\x00" * (
        fixture.GAP_SECTORS * ISO_SECTOR_SIZE + ISO_SECTOR_SIZE
    )
    with pytest.raises(RomError, match="allocation"):
        writer.rebuild_and_write({})


def test_a_copy_whose_archive_cannot_be_located_raises(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    out = Path(writer.output_path)
    image = bytearray(out.read_bytes())
    image[fixture.ISO_PVD_SECTOR * ISO_SECTOR_SIZE] = 2
    out.write_bytes(bytes(image))
    with pytest.raises(RomError, match="Cannot find db.viv"):
        writer.rebuild_and_write({})


def test_a_shorter_archive_has_its_directory_length_corrected(tmp_path):
    # A shrunk archive leaves the old bytes behind, which would still parse as
    # part of the last member. Both endian copies of the length are corrected;
    # this checks the little-endian one, which is what every reader here uses.
    from pathlib import Path

    writer = prepared(tmp_path)
    shorter = bytes(writer.db_viv)[:-4096]
    writer._db_viv = shorter
    writer.rebuild_and_write({})
    reader = NHL07PSPRomReader(str(Path(writer.output_path)))
    reader.load()
    assert reader.find_db_viv_location()[1] == len(shorter)


def test_a_shorter_archive_has_its_big_endian_directory_length_corrected(tmp_path):
    # The half no reader in this repository consults, and the half an image
    # would disagree with itself about if it were left alone.
    from pathlib import Path

    writer = prepared(tmp_path)
    shorter = bytes(writer.db_viv)[:-4096]
    writer._db_viv = shorter
    offset = writer.reader.find_db_viv_dir_entry_offset()
    writer.rebuild_and_write({})
    image = Path(writer.output_path).read_bytes()
    assert struct.unpack_from(">I", image, offset + 14)[0] == len(shorter)


def test_a_shorter_archive_zero_fills_the_bytes_it_no_longer_covers(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    shorter = bytes(writer.db_viv)[:-4096]
    writer._db_viv = shorter
    writer.rebuild_and_write({})
    image = Path(writer.output_path).read_bytes()
    start = fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE + len(shorter)
    assert image[start : start + 4096] == b"\x00" * 4096


def test_an_unchanged_archive_leaves_the_directory_length_alone(tmp_path):
    from pathlib import Path

    writer = prepared(tmp_path)
    offset = writer.reader.find_db_viv_dir_entry_offset()
    before = Path(writer.output_path).read_bytes()[offset + 10 : offset + 18]
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    after = Path(writer.output_path).read_bytes()[offset + 10 : offset + 18]
    assert after == before


def test_the_archive_bytes_a_caller_reads_back_are_the_ones_from_the_disc(tmp_path):
    # `db_viv` is the archive as `load` found it, before any edit. The edits
    # live in the parsed `TDBFile`s, and `rebuild_and_write` patches a copy.
    writer = prepared(tmp_path)
    before = writer.db_viv
    writer.write_player_bio(master(writer), 3, SKATER)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    assert writer.db_viv == before


def test_the_rebuilt_archive_is_flushed_to_disk(tmp_path):
    # `fsync`, deliberately: the SD cards these devices boot from are a real way
    # to read back a hole from an unflushed multi-hundred-megabyte write.

    writer = prepared(tmp_path)
    writer.write_player_bio(master(writer), 3, SKATER)
    writer.rebuild_and_write({TDB_MASTER: master(writer)})
    fd = os.open(writer.output_path, os.O_RDONLY)
    try:
        os.lseek(fd, fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE, os.SEEK_SET)
        assert os.read(fd, 4) == b"BIGF"
    finally:
        os.close(fd)


def test_the_copy_progress_span_ends_at_thirty_per_cent():
    # Kills `PROGRESS_COPY_END = 0.3` -> 0.35. The progress tests above compare
    # against the constant, which is the constant checked against itself: they
    # hold for any value it takes.
    assert PROGRESS_COPY_END == 0.3


def test_the_record_writing_span_ends_at_sixty_per_cent():
    # Kills `PROGRESS_RECORDS_END = 0.6` -> 0.55.
    assert PROGRESS_RECORDS_END == 0.6


def test_a_shorter_archive_zero_fills_bytes_that_were_not_already_zero(tmp_path):
    # Kills `remaining = db_orig_size - len(new_viv_bytes)` -> 0. The earlier
    # version of this test shortened the archive by 4 096 bytes, which lands
    # entirely inside the fixture's 8 192 bytes of trailing member slack -- so
    # the region was already zero and "it zero-filled" was true of a writer that
    # wrote nothing. Zero over zero, exactly.
    #
    # 10 000 reaches past the slack into the last member's RefPack stream.
    from pathlib import Path

    writer = prepared(tmp_path)
    original = bytes(writer.db_viv)
    dropped = original[len(original) - 10_000 :]
    assert dropped.count(0) < len(dropped)

    writer._db_viv = original[:-10_000]
    writer.rebuild_and_write({})
    image = Path(writer.output_path).read_bytes()
    start = fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE + len(original) - 10_000
    assert image[start : start + 10_000] == b"\x00" * 10_000
