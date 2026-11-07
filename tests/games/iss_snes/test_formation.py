"""ISS (SNES) formations: the record and the label a club is written, the eleven it is cut into and
who sits where.

The synthetic cartridge (`tests/fixtures/synthetic_iss_rom.py`) lays its formation table out as the
cartridge's: 28 pointers in slot order, then 28 records in name order (Scotland's after Cameroon's).
The tests that need the real cartridge are gated on RETRO_ROSTER_TEST_ROMS (a directory holding
iss.sfc).
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from retro_roster_patcher.core import kits as kits_module
from retro_roster_patcher.core.errors import MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, SlotMapping
from retro_roster_patcher.games.iss_snes.models import (
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TOTAL_TEAMS,
    ISSPlayerRecord,
    ISSTeamRecord,
    name_storage_index,
)
from retro_roster_patcher.games.iss_snes.patcher import ISSPatcher
from retro_roster_patcher.games.iss_snes.rom_reader import ISSRomReader
from retro_roster_patcher.games.iss_snes.rom_writer import ISSRomWriter
from retro_roster_patcher.games.iss_snes.stat_mapper import (
    FORMATION_DONORS,
    SLOT_SHAPES,
    ISSStatMapper,
    _donor_places,
)
from retro_roster_patcher.sports.lineup import pick_lineup, slot_role
from retro_roster_patcher.sports.models import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)
from tests.fixtures import synthetic_iss_rom as fixture
from tests.fixtures.older_roster import older_roster

# Italy's record (slot 1, at 0x38348) as the cartridge has it, and once England's 4-3-3 is written
# over it: sixteen of the 44 bytes change (the research report's test vector).
ITALY_442 = bytes.fromhex(
    "00700037 0c210132 0a510242 0a910242 0cc10132 12720374 "
    "18320444 18b20444 1a720574 22530600 22930600"
)
ITALY_433 = bytes.fromhex(
    "00700037 0e210132 0a510242 0a910242 0ec10132 14720374 "
    "18220454 18c20454 22230634 22c30630 24730630"
)

#: The shapes of the cartridge's 27 retail records, by the slots that hold them (the report's list).
RETAIL_SLOTS = {
    "5-3-2": (0, 17),
    "4-4-2": (1, 5, 6, 8, 10, 11, 12, 13, 14, 18, 19, 20, 22, 24),
    "3-4-3": (2, 3, 21),
    "4-3-3": (4, 7, 16),
    "4-5-1": (9, 15),
    "3-5-2": (23, 25),
    "4-2-4": (26,),
}
#: The slot whose retail record is each shape's donor.
DONOR_SLOTS = {
    "5-3-2": 0,
    "4-4-2": 6,
    "3-4-3": 2,
    "4-3-3": 4,
    "4-5-1": 9,
    "3-5-2": 23,
    "4-2-4": 26,
}
FORMATIONS = ("4-4-2", "4-3-3", "3-4-3", "5-3-2", "4-5-1", "3-5-2", "4-2-4")

GROUP = {
    "GK": "Goalkeeper",
    "RB": "Defender",
    "CB": "Defender",
    "LB": "Defender",
    "RWB": "Defender",
    "LWB": "Defender",
    "CDM": "Midfielder",
    "CM": "Midfielder",
    "CAM": "Midfielder",
    "RM": "Midfielder",
    "LM": "Midfielder",
    "RW": "Attacker",
    "LW": "Attacker",
    "ST": "Attacker",
}
#: The ESPN lineup code of each role.
CODE = {
    "GK": "G", "RB": "RB", "CB": "CD", "LB": "LB", "RWB": "RWB", "LWB": "LWB", "CDM": "DM",
    "CM": "CM", "CAM": "AM", "RM": "RM", "LM": "LM", "RW": "RW", "LW": "LW", "ST": "F",
}  # fmt: skip
#: The 4-3-3 that fills the back, midfield and attack with the roles that sit there.
XI_433 = "GK RB CB CB LB CDM CM CM RW ST LW"
BENCH = "GK CB CM ST"


@pytest.fixture(autouse=True)
def no_kit_catalogue(monkeypatch):
    """The test clubs' ids belong to catalogued clubs; their kits here come from their colours."""
    monkeypatch.setattr(kits_module, "_catalog", {"clubs": []})


@pytest.fixture
def mapper():
    return ISSStatMapper()


@pytest.fixture
def patcher(tmp_path):
    return ISSPatcher(cache_dir=tmp_path / "cache")


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


def squad(
    xi: str = XI_433, formation: str | None = "4-3-3", *, roles: bool = True, bench: str = BENCH
) -> TeamRoster:
    """A club whose players, in this order, have the roles of `xi` and then `bench`, each named by
    his role and his place in the list (GK0, LB4, ...). `roles=False` leaves them without a role,
    as a roster fetched before roles has them; `formation` None leaves the club without one."""
    given = [*xi.split(), *bench.split()]
    players = [
        Player(id=i + 1, name=f"{r}{i}", position=GROUP[r], role=r if roles else "")
        for i, r in enumerate(given)
    ]
    extra = {} if formation is None else {"formation": formation}
    return TeamRoster(team=Team(id=1, name="Club", code="CLB"), players=players, extra=extra)


def league(*rosters: TeamRoster) -> LeagueData:
    return LeagueData(league=League(id=1, name="League", season=2025), teams=list(rosters))


def mapped(patcher, roster, slot=0, **kwargs) -> MappedRosters:
    mapping = [SlotMapping(slot_index=slot, team_id=roster.team.id)]
    return patcher.map_rosters(league(roster), mapping, **kwargs)


def patch(patcher, rom, out, rosters) -> bytes:
    patcher.patch(
        rom_path=rom, output_path=out, rosters=rosters, kits=False, logos=False, looks=False
    )
    return out.read_bytes()


def record_of(image, slot) -> bytes:
    at = fixture.formation_offset(slot)
    return bytes(image[at : at + 44])


def label_of(image, slot) -> bytes:
    at = fixture.desc_offset(slot) + 2
    return bytes(image[at : at + 15])


def names_of(path, slot) -> list[str]:
    return ISSRomReader(str(path)).read_player_names()[name_storage_index(slot)]


def lines_of(record: bytes) -> list[int]:
    """The line of each of the eleven places: byte 1's low two bits (0 keeper, 1 defence...)."""
    return [record[4 * k + 1] & 3 for k in range(11)]


def lineup_of(players: list[Player], shape: str) -> dict:
    """`get_lineup`'s result for a club that started these players, in this order."""
    starters = [{"id": p.id, "position": CODE[p.role]} for p in players]
    return {"formation": shape, "shape": shape, "event": 1, "starters": starters}


def written_names(record: ISSTeamRecord) -> list[str]:
    return [p.name for p in record.players]


FIXTURE = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "lineups_2026-11-02.json").read_text(encoding="utf-8")
)
#: The eleven of the emulator-checked cartridge of Task 15 (its second one, `t15-galo-433-roleorder
#: -in-germany.sfc`), who stood in Germany's slot in this order: the keeper, the back four from the
#: left, the midfield and the front three from the left. Their names there are
#: `GALO_ON_THE_CARTRIDGE` (Vitor, on the checked one, where this squad, with no Victor Hugo, has
#: V.Hugo).
GALO_ROLE_ORDER = [
    "Éverson", "Renan Lodi", "Vitor Hugo", "Ruan", "Natanael", "Alan Franco", "Fred",
    "Igor Gomes", "Bernard", "Thiago Borbas", "Tomás Cuello",
]  # fmt: skip
GALO_ON_THE_CARTRIDGE = [
    "Everson", "R.Lodi", "V.Hugo", "Ruan", "Natanael", "A.Franco", "Fred", "I.Gomes", "Bernard",
    "T.Cuello", "T.Borbas",
]  # fmt: skip


def galo(*, fetched_with_roles: bool) -> TeamRoster:
    """Atlético-MG of the 2026-11-02 snapshot, the Galo of Task 15: a 4-2-3-1 the lineup folds to a
    4-3-3. As a fetch of that day left it, no role on anyone and no `extra["formation"]`, only the
    lineup; or, `fetched_with_roles`, as Task 15 built it: a role on each starter, read from his
    ESPN code, and the formation 4-3-3."""
    t = FIXTURE["teams"]["7632"]
    name = {int(k): v for k, v in t["names"].items()}
    positions = {int(k): v for k, v in t["positions"].items()}
    lineup = pick_lineup(t["matches"], dict.fromkeys(t["squad"], ""), positions)
    players = [
        Player(id=i, name=name[i], position=GROUP[lineup["roles"].get(str(i), "CM")])
        for i in t["squad"]
    ]
    roster = TeamRoster(
        team=Team(id=7632, name="Atletico MG", code="CAM"),
        players=players,
        extra={"lineup": lineup},
    )
    if fetched_with_roles:
        by_id = {p.id: p for p in players}
        for starter in lineup["starters"]:
            by_id[starter["id"]].role = slot_role(lineup["formation"], starter["position"])
        roster.extra["formation"] = "4-3-3"
    return roster


def slot_records(image: bytes, slot: int) -> dict[str, bytes]:
    """What a slot holds that the club decides, found through the cartridge's own pointers: its
    fifteen players' data (6 bytes each) and names (8), its formation record (44) and the first
    twenty bytes of its description block, whose label ends in the shape."""
    first = slot * PLAYERS_PER_TEAM
    data = fixture.OFS_PLAYER_DATA + 6 * first
    names = fixture.OFS_PLAYER_NAMES + 8 * name_storage_index(slot) * PLAYERS_PER_TEAM
    record_pointer = struct.unpack_from("<H", image, fixture.OFS_FORMATION_PTRS + 2 * slot)[0]
    record = 0x38000 + record_pointer - 0x8000
    label_pointer = struct.unpack_from("<H", image, fixture.OFS_DESC_PTRS + 2 * slot)[0]
    label = 0x10000 + label_pointer - 0x8000
    return {
        "data": bytes(image[data : data + 6 * PLAYERS_PER_TEAM]),
        "names": bytes(image[names : names + 8 * PLAYERS_PER_TEAM]),
        "formation": bytes(image[record : record + 44]),
        "label": bytes(image[label : label + 20]),
    }


def real_rom(test):
    gated = pytest.mark.skipif(
        not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
        reason="set RETRO_ROSTER_TEST_ROMS to a directory holding iss.sfc",
    )
    return pytest.mark.real_rom(gated(test))


@pytest.fixture
def retail() -> bytes:
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss.sfc"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    return rom.read_bytes()


# --- what the patcher says it writes -------------------------------------------------------------


def test_the_donors_are_the_patchers_shapes_in_its_order():
    assert tuple(FORMATION_DONORS) == ISSPatcher.formations == FORMATIONS


def test_a_donor_is_eleven_places_of_four_bytes_ordered_by_line_as_its_name_says():
    for shape, record in FORMATION_DONORS.items():
        assert len(record) == 44, shape
        lines = lines_of(record)
        assert lines == sorted(lines), shape
        assert lines[0] == 0 and 0 not in lines[1:], shape
        counts = tuple(lines.count(line) for line in (1, 2, 3))
        assert "-".join(map(str, counts)) == shape


def test_the_donors_keeper_is_place_zero_with_the_keepers_role_code():
    for shape, record in FORMATION_DONORS.items():
        assert record[2] == 0, shape
        assert all(record[4 * k + 2] != 0 for k in range(1, 11)), shape


def test_the_england_donor_is_the_test_vectors_after():
    assert FORMATION_DONORS["4-3-3"] == ITALY_433


def test_every_slot_has_a_shape_among_the_donors_and_it_is_the_reports():
    assert len(SLOT_SHAPES) == TOTAL_TEAMS
    expected = {slot: shape for shape, slots in RETAIL_SLOTS.items() for slot in slots}
    assert [expected[slot] for slot in range(TOTAL_TEAMS)] == list(SLOT_SHAPES)
    assert set(SLOT_SHAPES) <= set(FORMATION_DONORS)


# The lateral of each place of every donor, line by line, from the retail records: 1 to 13 across
# the pitch, 7 the middle and the low ones the team's left.
LATERALS = {
    "4-4-2": ((2, 5, 9, 12), (2, 5, 9, 12), (5, 9)),
    "4-3-3": ((2, 5, 9, 12), (7, 2, 12), (2, 12, 7)),
    "3-4-3": ((3, 7, 11), (7, 2, 12, 7), (2, 12, 7)),
    "5-3-2": ((1, 4, 7, 10, 13), (7, 3, 11), (4, 10)),
    "4-5-1": ((2, 5, 9, 12), (5, 9, 2, 12, 8), (6,)),
    "3-5-2": ((3, 7, 11), (2, 12, 7, 3, 11), (4, 10)),
    "4-2-4": ((2, 5, 9, 12), (4, 10), (2, 5, 9, 12)),
}


@pytest.mark.parametrize("shape", FORMATIONS)
def test_a_donor_place_is_as_wide_as_its_lateral_is_far_from_the_middle(shape):
    places = _donor_places(shape)
    assert [line for line, _, _ in places] == lines_of(FORMATION_DONORS[shape])
    for line in (1, 2, 3):
        sides = [side for ln, side, _ in places if ln == line]
        assert sides == pytest.approx([(lateral - 7) / 6 for lateral in LATERALS[shape][line - 1]])


def test_a_donor_place_has_the_depth_of_its_first_byte_and_a_side_from_its_lateral():
    places = _donor_places("4-3-3")
    assert [(line, depth) for line, _, depth in places] == [
        (0, 0x00),
        (1, 0x0E), (1, 0x0A), (1, 0x0A), (1, 0x0E),
        (2, 0x14), (2, 0x18), (2, 0x18),
        (3, 0x22), (3, 0x22), (3, 0x24),
    ]  # fmt: skip
    assert [side for _, side, _ in places] == pytest.approx(
        [0, -5 / 6, -2 / 6, 2 / 6, 5 / 6, 0, -5 / 6, 5 / 6, -5 / 6, 5 / 6, 0]
    )


def test_germanys_wing_backs_are_the_widest_places_of_any_donor_and_stand_on_the_touchlines():
    sides = [side for shape in FORMATIONS for _, side, _ in _donor_places(shape)]
    assert min(sides) == -1 and max(sides) == 1
    assert [side for _, side, _ in _donor_places("5-3-2")][1:6] == pytest.approx(
        [-1, -0.5, 0, 0.5, 1]
    )


# --- the writer ----------------------------------------------------------------------------------


def test_the_table_puts_italy_at_0x38348_and_scotland_after_cameroon_at_0x3873c(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_formation(1, ITALY_433)
    writer.write_formation(5, FORMATION_DONORS["3-5-2"])
    writer.finalize()
    image = out.read_bytes()
    assert image[0x38348 : 0x38348 + 44] == ITALY_433
    assert image[0x3873C : 0x3873C + 44] == FORMATION_DONORS["3-5-2"]


def test_a_formation_write_changes_its_44_bytes_and_nothing_else(rom, out):
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_formation(1, ITALY_433)
    writer.finalize()
    changed = [i for i, (a, b) in enumerate(zip(before, out.read_bytes(), strict=True)) if a != b]
    assert changed and 0x38348 <= changed[0] and changed[-1] < 0x38348 + 44


def test_a_formation_write_lands_past_a_copier_header(tmp_path, out):
    rom = fixture.write_iss_rom(tmp_path / "iss.smc", with_header=True)
    writer = ISSRomWriter(str(rom), str(out), header_offset=fixture.SMC_HEADER_SIZE)
    writer.write_formation(1, ITALY_433)
    writer.finalize()
    at = fixture.SMC_HEADER_SIZE + 0x38348
    assert out.read_bytes()[at : at + 44] == ITALY_433


@pytest.mark.parametrize("size", [0, 43, 45, 88])
def test_a_record_that_is_not_44_bytes_is_refused(rom, out, size):
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(ValueError):
        writer.write_formation(1, bytes(size))
    writer.close()
    assert out.read_bytes() == rom.read_bytes()


@pytest.mark.parametrize("slot", [27, 28, -1])
def test_the_record_no_team_owns_cannot_be_written(rom, out, slot):
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(IndexError):
        writer.write_formation(slot, ITALY_433)
    writer.close()
    assert out.read_bytes() == rom.read_bytes()


@pytest.mark.parametrize("pointer", [0x0000, 0x4000, 0x831B, 0x87C1, 0xFFFF])
def test_a_pointer_outside_the_teams_records_is_refused_not_followed(tmp_path, out, pointer):
    image = fixture.build_iss_rom()
    struct.pack_into("<H", image, fixture.OFS_FORMATION_PTRS + 2 * 3, pointer)
    rom = tmp_path / "bad.sfc"
    rom.write_bytes(bytes(image))
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(RomError, match="formation"):
        writer.write_formation(3, ITALY_433)
    writer.close()
    assert out.read_bytes() == rom.read_bytes()


def test_the_label_is_five_characters_at_the_eleventh_byte_of_the_description_block(rom, out):
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_formation_label(1, "4-3-3")
    writer.finalize()
    image = out.read_bytes()
    block = fixture.desc_offset(1)
    assert image[block : block + 20] == b"\xfe\x2cOffense 4-3-3  \xfe\x20\xfd"
    changed = [i for i, (a, b) in enumerate(zip(before, image, strict=True)) if a != b]
    assert changed == [block + 10 + 2, block + 10 + 4]  # 4-4-2 -> 4-3-3: the middle digit, the last
    assert fixture.desc_shape_offset(1) == block + 10


@pytest.mark.parametrize("shape", FORMATIONS)
def test_every_shape_the_patcher_offers_fits_the_label_and_keeps_its_fifteen_characters(
    rom, out, shape
):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_formation_label(6, shape)
    writer.finalize()
    image = out.read_bytes()
    assert label_of(image, 6) == f"Offense {shape}  ".encode()
    assert len(label_of(image, 6)) == 15
    assert image[fixture.desc_offset(6) + 1] == fixture.DESC_COLOUR
    assert image[fixture.desc_text_start(6) :][: fixture.DESC_TEXT_LENGTH] == fixture.desc_text(6)


@pytest.mark.parametrize("shape", ["", "4-3", "4-2-3-1", "4-3-3 "])
def test_a_shape_of_another_length_is_refused_for_the_label(rom, out, shape):
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(ValueError):
        writer.write_formation_label(1, shape)
    writer.close()
    assert out.read_bytes() == rom.read_bytes()


def test_a_description_pointer_below_bank_two_gets_no_label(tmp_path, out):
    """The same guard as the description's: such a pointer lands a bank low, on other tables."""
    rom = fixture.write_iss_rom(tmp_path / "bank.sfc", break_desc_bank=True)
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_formation_label(7, "4-3-3")
    writer.finalize()
    assert out.read_bytes() == rom.read_bytes()


def test_the_label_goes_past_a_copier_header(tmp_path, out):
    rom = fixture.write_iss_rom(tmp_path / "iss.smc", with_header=True)
    writer = ISSRomWriter(str(rom), str(out), header_offset=fixture.SMC_HEADER_SIZE)
    writer.write_formation_label(1, "3-5-2")
    writer.finalize()
    at = fixture.SMC_HEADER_SIZE + fixture.desc_shape_offset(1)
    assert out.read_bytes()[at : at + 5] == b"3-5-2"


def test_the_description_blurb_is_still_written_after_the_label(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_formation_label(0, "5-3-2")
    writer.write_team_descriptions({0: "Arsenal"})
    writer.finalize()
    image = out.read_bytes()
    expected = fixture.centred_description("Arsenal")
    assert image[fixture.desc_text_start(0) :][: len(expected)] == expected
    assert label_of(image, 0) == b"Offense 5-3-2  "


# --- the shape a club is written ----------------------------------------------------------------


def with_extra(roster: TeamRoster, **extra) -> TeamRoster:
    roster.extra = dict(extra)
    return roster


@pytest.mark.parametrize(
    ("extra", "slot", "shape"),
    [
        # The club's formation, folded and read for the nearest shape the cartridge has.
        ({"formation": "4-3-3"}, None, "4-3-3"),
        ({"formation": "4-2-3-1"}, None, "4-3-3"),
        ({"formation": "3-4-2-1"}, 0, "3-4-3"),
        ({"formation": " 3-5-2 "}, 0, "3-5-2"),
        ({"formation": "3-6-1"}, 0, "3-5-2"),  # as near 4-5-1, but the back three stays
        ({"formation": "5-4-1"}, 0, "5-3-2"),  # as near 4-4-2, but the back five stays
        ({"formation": "6-3-1"}, 9, "5-3-2"),
        # The formation wins over the lineup's shape.
        ({"formation": "4-3-3", "lineup": {"shape": "5-3-2"}}, 9, "4-3-3"),
        # A formation that cannot be read gives way to the lineup's shape, if that can be read ...
        ({"formation": "", "lineup": {"shape": "3-5-2"}}, 0, "3-5-2"),
        ({"formation": "4-4-x", "lineup": {"shape": " 4-3-3"}}, 1, "4-3-3"),
        ({"formation": None, "lineup": {"shape": "3-4-3"}}, 1, "3-4-3"),
        ({"lineup": {"shape": "4-2-3-1"}}, 1, "4-3-3"),
        # ... and both to the slot's own retail shape ...
        ({}, 0, "5-3-2"),
        ({}, 1, "4-4-2"),
        ({}, 9, "4-5-1"),
        ({}, 23, "3-5-2"),
        ({}, 26, "4-2-4"),
        ({"formation": "oops", "lineup": {"shape": "x"}}, 23, "3-5-2"),
        ({"formation": 442, "lineup": {}}, 26, "4-2-4"),
        # ... and with no slot to the first the patcher offers.
        ({}, None, "4-4-2"),
        ({"formation": "oops"}, None, "4-4-2"),
    ],
)
def test_the_shape_is_the_formation_else_the_lineups_else_the_slots_own(mapper, extra, slot, shape):
    roster = with_extra(squad(), **extra)
    assert mapper._shape(roster, slot) == shape


def test_a_mapped_record_carries_the_shape_it_is_ordered_for(patcher):
    roster = squad(formation="3-5-2")
    assert mapped(patcher, roster, slot=0).teams[0].formation == "3-5-2"
    roster = squad(formation=None)
    assert mapped(patcher, roster, slot=17).teams[17].formation == "5-3-2"
    assert mapped(patcher, roster, slot=2).teams[2].formation == "3-4-3"


# --- the eleven ----------------------------------------------------------------------------------


def positions_of(players: list[Player]) -> list[str]:
    return [p.position for p in players]


def big_squad() -> list[Player]:
    groups = ["Goalkeeper"] * 3 + ["Defender"] * 8 + ["Midfielder"] * 8 + ["Attacker"] * 5
    return [Player(id=i + 1, name=f"P{i + 1}", position=g) for i, g in enumerate(groups)]


@pytest.mark.parametrize("shape", FORMATIONS)
def test_the_eleven_has_the_players_per_line_of_the_shape_it_is_written(mapper, shape):
    d, m, f = map(int, shape.split("-"))
    eleven = mapper._select_best_15(big_squad(), {}, None, shape)[:11]
    assert positions_of(eleven) == (
        ["Goalkeeper"] + ["Defender"] * d + ["Midfielder"] * m + ["Attacker"] * f
    )


def test_without_a_shape_the_eleven_is_the_old_four_four_two(mapper):
    eleven = mapper._select_best_15(big_squad(), {})[:11]
    assert (
        positions_of(eleven)
        == ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Attacker"] * 2
    )


def test_a_front_three_keeps_all_three_in_attack_in_a_four_three_three(mapper):
    players = squad("GK RB CB CB LB CDM CM CM RW ST LW", bench="GK CB CM ST CM").players
    lineup = lineup_of(players[:11], "4-3-3")
    eleven = mapper._select_best_15(players, {}, lineup, "4-3-3")[:11]
    assert {p.id for p in eleven} == {p.id for p in players[:11]}
    assert positions_of(eleven)[-3:] == ["Attacker"] * 3
    # The same eleven in a 4-4-2 sends the third forward to midfield, as it always did.
    four = mapper._select_best_15(players, {}, lineup, "4-4-2")[:11]
    assert {p.id for p in four} == {p.id for p in players[:11]}
    assert positions_of(four)[-2:] == ["Attacker"] * 2
    assert positions_of(four)[5:9].count("Attacker") == 1


def test_a_back_five_takes_a_fifth_defender_from_the_pool(mapper):
    eleven = mapper._select_best_15(big_squad(), {}, None, "5-3-2")[:11]
    assert positions_of(eleven)[1:6] == ["Defender"] * 5


# --- the order the caller gives ------------------------------------------------------------------


def test_the_given_order_keeps_the_keeper_first_and_the_outfield_as_given(mapper):
    players = squad("CB CB CB CB CM CM CM CM ST ST GK", bench="GK CB CM ST").players
    assert [p.name for p in mapper._given(players)[:11]] == [
        "GK10", "CB0", "CB1", "CB2", "CB3", "CM4", "CM5", "CM6", "CM7", "ST8", "ST9",
    ]  # fmt: skip


def test_a_second_keeper_among_the_first_eleven_makes_way_for_the_next_outfielder(mapper):
    players = squad("GK CB CB GK CB CM CM CM CM ST ST", bench="CM ST GK CB").players
    xi = mapper._given(players)[:11]
    assert [p.name for p in xi] == [
        "GK0", "CB1", "CB2", "CB4", "CM5", "CM6", "CM7", "CM8", "ST9", "ST10", "CM11",
    ]  # fmt: skip
    # The keeper who made way is on the bench where the caller had him.
    assert [p.name for p in mapper._given(players)[11:]] == ["GK3", "ST12", "GK13", "CB14"]


def test_a_squad_without_a_keeper_puts_its_first_player_in_goal(mapper):
    players = squad("CB CB CB CB CM CM CM CM ST ST CM", bench="CB CM ST CM").players
    assert [p.name for p in mapper._given(players)] == [p.name for p in players][:15]


def test_the_given_fifteen_is_cut_at_fifteen(mapper):
    players = squad(bench="GK CB CM ST CM CM CM").players
    assert len(mapper._given(players)) == PLAYERS_PER_TEAM


def test_changing_the_formation_keeps_the_given_order(patcher):
    """Ten central midfielders, so only the order and the places can decide who stands where."""
    roster = squad("GK " + "CM " * 10)
    for shape in FORMATIONS:
        roster.extra["formation"] = shape
        record = mapped(patcher, roster, order_as_given=True).teams[0]
        assert record.formation == shape
        assert written_names(record)[:11] == [p.name for p in roster.players[:11]], shape


def test_each_places_line_comes_from_the_formation_by_index(patcher):
    roster = squad("GK " + "CM " * 10)
    # The fourth outfielder is the last defender of a back four, the first midfielder of a back
    # three: same man, same place in the record, another line.
    lines = {}
    for shape in ("4-4-2", "3-4-3", "5-3-2"):
        roster.extra["formation"] = shape
        record = mapped(patcher, roster, order_as_given=True).teams[0]
        assert written_names(record)[4] == roster.players[4].name
        lines[shape] = lines_of(FORMATION_DONORS[record.formation])[4]
    assert lines == {"4-4-2": 1, "3-4-3": 2, "5-3-2": 1}


def test_a_formation_that_cannot_be_read_still_leaves_the_eleven_to_the_caller(patcher):
    """The lineup's 3-5-2 is the shape then, and the caller's ten outfielders are cut over it by
    index, as over a formation that reads."""
    roster = squad("GK " + "CM " * 10, formation="4-4-x")
    roster.extra["lineup"] = {"shape": "3-5-2", "formation": "3-5-2", "starters": []}
    record = mapped(patcher, roster, slot=0, order_as_given=True).teams[0]
    assert record.formation == "3-5-2"
    assert written_names(record)[:11] == [p.name for p in roster.players[:11]]
    # The fourth outfielder is the first midfielder of a 3-5-2 (the last defender of a 4-4-2).
    assert lines_of(FORMATION_DONORS[record.formation])[4] == 2


def test_with_no_lineup_either_the_slots_own_shape_cuts_the_callers_eleven(patcher):
    roster = squad("GK " + "CM " * 10, formation=None)
    record = mapped(patcher, roster, slot=17, order_as_given=True).teams[17]
    assert record.formation == "5-3-2"  # Russia's own
    assert written_names(record)[:11] == [p.name for p in roster.players[:11]]


def test_an_older_roster_edited_so_a_bench_player_stays_out_keeps_him_out_of_the_eleven(patcher):
    """No role, no formation, only a lineup whose shape is a 5-3-2, which wants a fifth defender:
    the caller's eleven has four and the fifth waits on the bench, and he stays there."""
    roster = squad("GK CB CB CB CB CM CM CM CM ST ST", formation=None, bench="GK CB CM ST")
    roster.extra = {"lineup": lineup_of(roster.players[:11], "5-3-2")}
    for player in roster.players:
        player.role = ""
    record = mapped(patcher, roster, slot=1, order_as_given=True).teams[1]
    names = written_names(record)
    assert record.formation == "5-3-2"  # the lineup's, not Italy's own 4-4-2
    assert names[:11] == [p.name for p in roster.players[:11]]
    assert "CB12" not in names[:11] and "CB12" in names[11:]


def test_a_given_squad_longer_than_fifteen_is_cut_there_and_a_keeper_listed_last_goes_first(
    patcher,
):
    roster = squad("CM " * 10 + "GK", bench="CM CM CM CM CM CM")
    record = mapped(patcher, roster, order_as_given=True).teams[0]
    assert len(record.players) == PLAYERS_PER_TEAM
    assert written_names(record)[0] == "GK10"


# --- who sits where ------------------------------------------------------------------------------


def xi_names(patcher, roster, **kwargs) -> list[str]:
    return written_names(mapped(patcher, roster, **kwargs).teams[0])[:11]


def test_the_back_four_takes_its_sides_whatever_order_it_is_given_in(patcher):
    for back in ("RB CB LB CB", "LB CB CB RB", "CB RB CB LB"):
        roster = squad(f"GK {back} CDM CM CM RW ST LW")
        names = xi_names(patcher, roster, order_as_given=True)
        roles = [n.rstrip("0123456789") for n in names]
        # England's back four: the left back (lateral 2), two centre backs, the right back (12).
        assert roles[1:5] == ["LB", "CB", "CB", "RB"], back


def test_the_front_three_takes_the_left_the_right_and_the_middle_of_englands_attack(patcher):
    for front in ("ST RW LW", "LW RW ST", "RW ST LW"):
        roster = squad(f"GK RB CB CB LB CDM CM CM {front}")
        names = xi_names(patcher, roster, order_as_given=True)
        roles = [n.rstrip("0123456789") for n in names]
        # Entries 8, 9 and 10 stand on the left (2), the right (12) and the middle (7).
        assert roles[8:11] == ["LW", "RW", "ST"], front


def test_the_deeper_midfielder_takes_the_deeper_place_and_the_wide_ones_the_sides(patcher):
    for middle in ("LM CDM RM", "RM LM CDM", "CDM RM LM"):
        roster = squad(f"GK RB CB CB LB {middle} RW ST LW")
        names = xi_names(patcher, roster, order_as_given=True)
        roles = [n.rstrip("0123456789") for n in names]
        # England's midfield: a holding place in the middle, then one on each side, a step higher.
        assert roles[5:8] == ["CDM", "LM", "RM"], middle


def test_a_back_five_seats_its_wing_backs_wide_and_deep_and_its_centre_backs_inside(patcher):
    for back in ("RWB CB CB CB LWB", "CB LWB CB RWB CB"):
        roster = squad(f"GK {back} CM CM CM ST ST", formation="5-3-2")
        names = xi_names(patcher, roster, order_as_given=True)
        roles = [n.rstrip("0123456789") for n in names]
        assert roles[1] == "LWB" and roles[5] == "RWB", back
        assert roles[2:5] == ["CB"] * 3, back


def test_a_back_five_seats_its_full_backs_on_the_touchline_places(patcher):
    # A full back is no deeper than a centre back, so only the width tells his place from the
    # inner one next to it: Germany's laterals are 1, 4, 7, 10 and 13.
    for back in ("CB LB CB RB CB", "LB CB CB CB RB", "RB CB CB CB LB", "CB CB CB RB LB"):
        roster = squad(f"GK {back} CM CM CM ST ST", formation="5-3-2")
        names = xi_names(patcher, roster, order_as_given=True)
        roles = [n.rstrip("0123456789") for n in names]
        assert roles[1] == "LB" and roles[5] == "RB", back
        assert roles[2:5] == ["CB"] * 3, back


def test_players_without_a_role_stay_as_given_inside_their_line(patcher):
    roster = squad(roles=False)
    names = xi_names(patcher, roster, order_as_given=True)
    assert names == [p.name for p in roster.players[:11]]


def test_an_unknown_role_is_seated_as_a_central_midfielder(patcher):
    roster = squad()
    roster.players[2].role = "SWEEPER"
    odd = xi_names(patcher, roster, order_as_given=True)
    roster.players[2].role = "CM"
    assert odd == xi_names(patcher, roster, order_as_given=True)


def test_seating_moves_players_inside_a_line_and_never_across_lines(patcher):
    roster = squad("GK RB CB CB LB CDM CM CM ST RW LW")
    names = xi_names(patcher, roster, order_as_given=True)
    given = [p.name for p in roster.players[:11]]
    for line in ((1, 5), (5, 8), (8, 11)):
        assert sorted(names[line[0] : line[1]]) == sorted(given[line[0] : line[1]])
    assert names[0] == given[0]


def test_the_automatic_eleven_is_seated_too(patcher):
    roster = squad("GK RB CB CB LB CDM CM CM ST RW LW")
    names = xi_names(patcher, roster)
    roles = [n.rstrip("0123456789") for n in names]
    assert roles[1:5] == ["LB", "CB", "CB", "RB"]
    assert roles[8:11] == ["LW", "RW", "ST"]


def test_a_short_squad_is_seated_as_far_as_it_goes(patcher):
    roster = squad("GK RB CB LB", bench="CM")
    names = written_names(mapped(patcher, roster).teams[0])
    assert names[0] == "GK0" and sorted(names) == sorted(p.name for p in roster.players)


# An older rosters file has no role on any player: the codes of its lineup are the roles.


def test_an_older_rosters_eleven_are_seated_by_the_roles_their_lineup_codes_read_as(patcher):
    roster = older_roster()
    # The squad listed in the lineup's own order, the right back before the left back and the
    # winger before the striker. The 4-3-3 over Germany's 5-3-2: from the left, the back four, the
    # holding midfielder in the middle and the wide pair, and the front three.
    assert xi_names(patcher, roster) == [
        "GK1", "LB3", "CB6", "CB5", "RB2", "CDM4", "CM8", "CM7", "LW11", "RW10", "ST9",
    ]  # fmt: skip


def test_an_older_rosters_edited_sheet_is_seated_by_the_same_roles(patcher):
    roster = older_roster()
    sheet = {p.name: p for p in roster.players}
    first = "GK1 RB2 CB5 CB6 LB3 CDM4 CM7 CM8 RW10 ST9 LW11".split()
    roster.players = [sheet[n] for n in first] + [p for p in roster.players if p.name not in first]
    record = mapped(patcher, roster, order_as_given=True).teams[0]
    assert record.formation == "4-3-3"
    names = written_names(record)
    assert [names[1], names[4]] == ["LB3", "RB2"]
    assert [names[8], names[9]] == ["LW11", "RW10"]


def test_the_suggested_order_of_an_older_roster_is_the_order_it_is_written_in(patcher):
    roster = older_roster()
    suggested = patcher.suggest_squad_order(roster)
    # From the left in each line, by the side of the lineup's codes: the left back, the left and
    # the right centre back (CD-L, CD-R), the right back, and so on.
    assert [p.name for p in suggested[:11]] == [
        "GK1", "LB3", "CB6", "CB5", "RB2", "CM8", "CDM4", "CM7", "LW11", "ST9", "RW10",
    ]  # fmt: skip
    roster.players = suggested
    assert xi_names(patcher, roster, order_as_given=True) == xi_names(patcher, older_roster())


def test_a_player_with_a_role_is_seated_by_it_whatever_his_lineup_code_says(patcher):
    roster = older_roster()
    for player in roster.players:
        player.role = "CM"
    assert xi_names(patcher, roster, order_as_given=True) == [p.name for p in roster.players[:11]]


def test_a_player_with_no_role_and_no_lineup_code_reads_as_his_squad_groups_role(patcher):
    # A 4-4-2 takes two of three attackers up front and sends the third to midfield, and the roles
    # say which: ST10 has none and is an Attacker, so a striker, further forward than the wingers.
    roster = squad("GK CB CB CB CB CM CM CM RW LW ST", formation="4-4-2", bench="GK")
    roster.players[10].role = ""
    eleven = [p.name for p in patcher.suggest_squad_order(roster)[:11]]
    assert sorted(eleven[5:9]) == ["CM5", "CM6", "CM7", "RW8"]
    assert sorted(eleven[9:]) == ["LW9", "ST10"]


def test_the_suggested_eleven_cuts_its_lines_by_the_depth_of_its_roles_not_its_lineup_groups(
    patcher,
):
    # ESPN groups a 4-2-3-1's wide attacking midfielders AM-R and AM-L, and the one behind the
    # striker, with the midfield: the 4-3-3 took two holding midfielders and the right winger as
    # its midfield and played the attacking midfielder up front.
    codes = "G RB LB LM CD-R CD-L AM-R RM F AM AM-L".split()
    roles = "GK RB LB CDM CB CB RW CDM ST CAM LW".split()
    roster = older_roster(codes, roles, formation="4-2-3-1", shape="4-3-3")
    names = [p.name for p in patcher.suggest_squad_order(roster)[:11]]
    kinds = [n.rstrip("0123456789") for n in names]
    assert sorted(kinds[1:5]) == ["CB", "CB", "LB", "RB"]
    assert sorted(kinds[5:8]) == ["CAM", "CDM", "CDM"]
    assert sorted(kinds[8:11]) == ["LW", "RW", "ST"]


def test_inside_a_line_the_suggested_order_runs_from_the_left_by_lineup_code(patcher):
    names = [p.name for p in patcher.suggest_squad_order(older_roster())[:11]]
    # The left back, the left centre back, the right centre back, the right back: ESPN's -L and
    # -R say which of two centre backs is on which side, which his role (CB) does not.
    assert names[1:5] == ["LB3", "CB6", "CB5", "RB2"]
    assert names[8:11] == ["LW11", "ST9", "RW10"]


def test_players_of_one_depth_and_one_side_keep_the_lineups_order(patcher):
    # Three holding midfielders at the same code: nothing tells them apart but the lineup's order.
    codes = "G RB LB CM CD-R CD-L CM CM F RF LF".split()
    names = [p.name for p in patcher.suggest_squad_order(older_roster(codes))[:11]]
    assert names[5:8] == ["CDM4", "CM7", "CM8"]


def stats_of(player_id: int, **kwargs) -> PlayerStats:
    base = dict(
        appearances=20, minutes=1600, goals=5, assists=3, shots_total=30, shots_on=12,
        passes_total=600, passes_accuracy=80.0, tackles_total=20, interceptions=15, blocks=5,
        duels_total=100, duels_won=55, dribbles_attempts=40, dribbles_success=20,
        fouls_committed=15, fouls_drawn=12, cards_yellow=3, cards_red=0, rating=None, lineups=18,
    )  # fmt: skip
    base.update(kwargs)
    return PlayerStats(player_id=player_id, **base)


def test_a_player_only_the_slots_own_shape_picks_is_still_ranked(mapper):
    """With nothing to say what it plays, Germany's slot is a 5-3-2: its fifth defender is one a
    4-4-2 leaves out of the fifteen, and the fifteens that set the scale must include him or his
    goals count for nothing."""
    groups = ["Goalkeeper"] * 2 + ["Defender"] * 6 + ["Midfielder"] * 6 + ["Attacker"] * 4
    players = [Player(id=i + 1, name=f"P{i + 1}", position=g) for i, g in enumerate(groups)]
    # Starts fall along the list but for the fifth and sixth defenders (7 and 8), who started the
    # least: a 4-4-2 takes the keepers, four defenders, four midfielders and two forwards, then
    # the two midfielders and forwards left over as its subs.
    lineups = {p.id: 40 - p.id for p in players} | {7: 3, 8: 2}
    stats = {p.id: stats_of(p.id, lineups=lineups[p.id], goals=p.id % 3) for p in players}
    stats[7] = stats_of(7, lineups=3, goals=99, shots_on=99)
    roster = TeamRoster(
        team=Team(id=1, name="Club", code="CLB"), players=players, player_stats=stats
    )
    four = mapper.map_team_with_league_context(roster, [roster])
    assert 7 not in {p.player_id for p in four.players}
    five = mapper.map_team_with_league_context(roster, [roster], slot=0)
    assert five.formation == "5-3-2"
    scorer = next(p for p in five.players if p.player_id == 7)
    assert scorer.attributes.shooting >= 6


def test_the_suggested_order_follows_the_clubs_shape(patcher):
    roster = squad(formation="3-5-2", bench="GK CB CM ST CB CM")
    suggested = patcher.suggest_squad_order(roster)
    assert positions_of(suggested[:11]) == (
        ["Goalkeeper"] + ["Defender"] * 3 + ["Midfielder"] * 5 + ["Attacker"] * 2
    )
    assert len(suggested) == len(roster.players)


# --- the patch -----------------------------------------------------------------------------------


def test_writing_a_four_three_three_to_italy_gives_the_test_vector(patcher, rom, out):
    image = patch(patcher, rom, out, mapped(patcher, squad(), slot=1))
    assert record_of(image, 1) == ITALY_433
    assert image[0x38348 : 0x38348 + 44] == ITALY_433


def test_a_club_with_a_front_three_in_germanys_back_five_plays_four_three_three(patcher, rom, out):
    """The tester's report, issue #8: a United side in a 5-3-2 slot left its winger at the back."""
    roster = squad("GK RB CB CB LB CDM CM CM ST RW LW", formation="4-2-3-1")
    image = patch(patcher, rom, out, mapped(patcher, roster, slot=0))
    assert record_of(image, 0) == ITALY_433
    assert lines_of(record_of(image, 0)) == [0, 1, 1, 1, 1, 2, 2, 2, 3, 3, 3]
    names = names_of(out, 0)
    assert names[8:11] == ["LW10", "RW9", "ST8"]
    assert {n.rstrip("0123456789") for n in names[1:5]} == {"RB", "CB", "LB"}


def test_the_label_shows_the_clubs_shape(patcher, rom, out):
    image = patch(patcher, rom, out, mapped(patcher, squad(formation="3-5-2"), slot=0))
    assert label_of(image, 0) == b"Offense 3-5-2  "
    assert image[fixture.desc_offset(0) + 1] == fixture.DESC_COLOUR


def test_the_record_and_the_label_agree_for_every_shape(patcher, rom, out):
    for shape in FORMATIONS:
        image = patch(patcher, rom, out, mapped(patcher, squad(formation=shape), slot=6))
        assert record_of(image, 6) == FORMATION_DONORS[shape], shape
        assert label_of(image, 6) == f"Offense {shape}  ".encode(), shape


def test_unmapped_slots_and_the_record_no_team_owns_keep_their_formation(patcher, rom, out):
    before = rom.read_bytes()
    image = patch(patcher, rom, out, mapped(patcher, squad(), slot=1))
    for slot in range(TOTAL_TEAMS):
        if slot != 1:
            assert record_of(image, slot) == record_of(before, slot), slot
            assert label_of(image, slot) == label_of(before, slot), slot
    spare = fixture.OFS_FORMATION_RECORDS + 27 * 44
    assert image[spare : spare + 44] == before[spare : spare + 44]


def test_a_blanked_unused_slot_keeps_its_formation_too(patcher, rom, out):
    before = rom.read_bytes()
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=mapped(patcher, squad(), slot=1),
        kits=False,
        logos=False,
        looks=False,
        blank_unused=True,
    )
    image = out.read_bytes()
    assert record_of(image, 2) == record_of(before, 2)
    assert label_of(image, 2) == label_of(before, 2)


def test_a_record_with_no_formation_leaves_the_images_own_record_and_label(patcher, rom, out):
    """A caller that builds the records itself has not said what the team plays."""
    before = rom.read_bytes()
    team = ISSTeamRecord(name="Hand", short_name="HND", players=[ISSPlayerRecord(name="X")])
    assert team.formation == ""
    image = patch(patcher, rom, out, MappedRosters(game_id="iss-snes", teams={1: team}))
    assert record_of(image, 1) == record_of(before, 1)
    assert label_of(image, 1) == label_of(before, 1)


def test_a_shape_the_cartridge_cannot_hold_is_refused_before_the_rom_is_touched(patcher, rom, out):
    team = ISSTeamRecord(name="Odd", short_name="ODD", players=[], formation="4-2-3-1")
    with pytest.raises(MappingError, match="4-2-3-1"):
        patch(patcher, rom, out, MappedRosters(game_id="iss-snes", teams={1: team}))
    assert not out.exists()


def test_patching_twice_gives_the_same_image(patcher, rom, tmp_path):
    rosters = mapped(patcher, squad(), slot=0)
    first = patch(patcher, rom, tmp_path / "first.sfc", rosters)
    second = patch(patcher, rom, tmp_path / "second.sfc", rosters)
    assert first == second


def test_patching_a_patched_image_again_leaves_every_record_and_label_where_they_were(
    patcher, rom, tmp_path
):
    rosters = mapped(patcher, squad(), slot=0)
    first = patch(patcher, rom, tmp_path / "first.sfc", rosters)
    again = patch(patcher, tmp_path / "first.sfc", tmp_path / "again.sfc", rosters)
    table = slice(fixture.OFS_FORMATION_RECORDS, fixture.OFS_FORMATION_RECORDS + 28 * 44)
    assert again[table] == first[table]
    for slot in range(TOTAL_TEAMS):
        assert label_of(again, slot) == label_of(first, slot), slot


def test_a_repatch_writes_the_retail_donor_not_a_slot_the_first_patch_rewrote(
    patcher, rom, tmp_path
):
    """England's slot holds the 4-3-3 donor until a club takes it in a 3-5-2; the next club that
    plays a 4-3-3 still gets England's retail record, as the test vector has it."""
    england = squad(formation="3-5-2")
    first = patch(patcher, rom, tmp_path / "first.sfc", mapped(patcher, england, slot=4))
    assert record_of(first, 4) == FORMATION_DONORS["3-5-2"]
    second = patch(
        patcher, tmp_path / "first.sfc", tmp_path / "second.sfc", mapped(patcher, squad(), slot=1)
    )
    assert record_of(second, 1) == ITALY_433
    assert record_of(second, 4) == FORMATION_DONORS["3-5-2"]


def test_a_roster_fetched_before_roles_and_formations_still_patches(patcher, rom, out):
    """No role, no formation, only a lineup with a shape: the lineup's shape is the club's."""
    roster = squad("GK CB CB CB CM CM CM CM CM ST ST", formation=None)
    roster.extra = {"lineup": lineup_of(roster.players[:11], "3-5-2")}
    for player in roster.players:
        player.role = ""
    image = patch(patcher, rom, out, mapped(patcher, roster, slot=0))
    assert record_of(image, 0) == FORMATION_DONORS["3-5-2"]
    assert label_of(image, 0) == b"Offense 3-5-2  "
    assert names_of(out, 0)[:11] == [p.name for p in roster.players[:11]]


def test_a_roster_with_nothing_but_players_plays_the_slots_own_shape(patcher, rom, out):
    roster = squad(formation=None, roles=False)
    image = patch(patcher, rom, out, mapped(patcher, roster, slot=0))
    assert record_of(image, 0) == FORMATION_DONORS["5-3-2"]
    assert label_of(image, 0) == b"Offense 5-3-2  "


def patched_galo(patcher, rom, out, *, fetched_with_roles, as_suggested) -> dict[str, bytes]:
    """Slot 0's records after the Galo goes into Germany's slot: the squad as the editor hands it
    back (`suggest_squad_order`, patched `order_as_given`), or, not `as_suggested`, as the mapper
    orders it itself."""
    roster = galo(fetched_with_roles=fetched_with_roles)
    if as_suggested:
        roster.players = patcher.suggest_squad_order(roster)
    rosters = mapped(patcher, roster, order_as_given=as_suggested)
    return slot_records(patch(patcher, rom, out, rosters), 0)


@pytest.mark.parametrize("fetched_with_roles", [False, True], ids=["older", "with roles"])
def test_the_galo_through_the_suggested_order_gives_the_role_order_the_emulator_checked(
    patcher, rom, tmp_path, fetched_with_roles
):
    """Task 15 found the editor's order, by ESPN groups, put the right winger Cuello in midfield and
    the attacking midfielder Fred up front; the order by role gave the eleven the emulator then
    showed seated by role and side, in Germany's slot as a 4-3-3."""
    roster = galo(fetched_with_roles=fetched_with_roles)
    assert [p.name for p in patcher.suggest_squad_order(roster)[:11]] == GALO_ROLE_ORDER
    suggested = patched_galo(
        patcher, rom, tmp_path / "s.sfc", fetched_with_roles=fetched_with_roles, as_suggested=True
    )
    automatic = patched_galo(
        patcher, rom, tmp_path / "a.sfc", fetched_with_roles=fetched_with_roles, as_suggested=False
    )
    assert suggested == automatic  # what the editor hands back is what the mapper writes
    assert names_of(tmp_path / "s.sfc", 0)[:11] == GALO_ON_THE_CARTRIDGE
    assert suggested["formation"] == FORMATION_DONORS["4-3-3"]
    assert suggested["label"][2:17] == b"Offense 4-3-3  "


def test_a_roster_with_a_formation_of_its_own_keeps_it_over_its_lineups_shape(patcher, rom, out):
    roster = older_roster()
    roster.extra["formation"] = "3-5-2"
    image = patch(patcher, rom, out, mapped(patcher, roster, slot=1))
    assert record_of(image, 1) == FORMATION_DONORS["3-5-2"]


def test_an_older_rosters_lineup_shape_writes_its_record_and_label(patcher, rom, out):
    image = patch(patcher, rom, out, mapped(patcher, older_roster(), slot=1))
    assert record_of(image, 1) == ITALY_433
    assert label_of(image, 1) == b"Offense 4-3-3  "
    names = names_of(out, 1)
    assert [names[1], names[4]] == ["LB3", "RB2"]  # England's lateral 2 and 12
    assert [names[8], names[9], names[10]] == ["LW11", "RW10", "ST9"]


def test_with_neither_a_formation_nor_a_lineup_shape_the_slots_own_stays(patcher, rom, out):
    roster = older_roster(shape=None)
    assert "shape" not in roster.extra["lineup"]
    image = patch(patcher, rom, out, mapped(patcher, roster, slot=0))
    assert record_of(image, 0) == FORMATION_DONORS["5-3-2"]  # Germany's own
    assert label_of(image, 0) == b"Offense 5-3-2  "


# --- the real cartridge --------------------------------------------------------------------------


@real_rom
def test_the_cartridges_pointer_table_holds_28_records_44_bytes_apart(retail):
    pointers = [struct.unpack_from("<H", retail, 0x382E4 + 2 * i)[0] for i in range(28)]
    offsets = [0x38000 + p - 0x8000 for p in pointers]
    assert sorted(offsets) == [0x3831C + 44 * n for n in range(28)]
    # Italy's record, and Scotland's after Cameroon's: records are in the order of the names.
    assert offsets[1] == 0x38348
    assert offsets[5] == 0x3873C
    assert offsets[24] == 0x38710 and offsets[5] == offsets[24] + 44
    assert offsets[27] == 0x387C0


@real_rom
def test_each_donor_is_the_record_the_cartridge_holds_in_its_donor_slot(retail):
    for shape, slot in DONOR_SLOTS.items():
        at = 0x38000 + struct.unpack_from("<H", retail, 0x382E4 + 2 * slot)[0] - 0x8000
        assert FORMATION_DONORS[shape] == retail[at : at + 44], (shape, TEAM_ENUM_ORDER[slot])


@real_rom
def test_each_slots_shape_is_the_one_its_retail_records_lines_count(retail):
    for slot, shape in enumerate(SLOT_SHAPES):
        at = 0x38000 + struct.unpack_from("<H", retail, 0x382E4 + 2 * slot)[0] - 0x8000
        lines = lines_of(retail[at : at + 44])
        counts = tuple(lines.count(line) for line in (1, 2, 3))
        assert "-".join(map(str, counts)) == shape, TEAM_ENUM_ORDER[slot]


@real_rom
def test_the_widest_retail_laterals_are_6_from_the_middle_which_is_what_a_side_divides_by(retail):
    laterals = set()
    for slot in range(28):
        at = 0x38000 + struct.unpack_from("<H", retail, 0x382E4 + 2 * slot)[0] - 0x8000
        laterals |= {retail[at + 4 * k + 1] >> 4 for k in range(11)}
    assert 7 in laterals and (min(laterals), max(laterals)) == (7 - 6, 7 + 6)


@real_rom
def test_the_cartridges_labels_end_in_the_shape_of_the_slots_record(retail):
    for slot, shape in enumerate(SLOT_SHAPES):
        pointer = struct.unpack_from("<H", retail, fixture.OFS_DESC_PTRS + 2 * slot)[0]
        block = 0x10000 + pointer - 0x8000
        assert retail[block] == 0xFE and retail[block + 17] == 0xFE
        assert retail[block + 2 : block + 9] in (b"Offense", b"Defense"), slot
        assert retail[block + 10 : block + 15] == shape.encode(), TEAM_ENUM_ORDER[slot]


@real_rom
def test_writing_a_four_three_three_over_italy_changes_sixteen_bytes(retail, tmp_path):
    rom = tmp_path / "iss.sfc"
    rom.write_bytes(retail)
    assert retail[0x38348 : 0x38348 + 44] == ITALY_442
    out = tmp_path / "out.sfc"
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_formation(1, FORMATION_DONORS["4-3-3"])
    writer.finalize()
    image = out.read_bytes()
    assert image[0x38348 : 0x38348 + 44] == ITALY_433
    changed = [i for i, (a, b) in enumerate(zip(retail, image, strict=True)) if a != b]
    assert len(changed) == 16
    assert all(0x38348 <= i < 0x38348 + 44 for i in changed)


@real_rom
def test_a_patch_of_the_cartridge_writes_the_record_and_the_label(retail, tmp_path):
    rom = tmp_path / "iss.sfc"
    rom.write_bytes(retail)
    out = tmp_path / "out.sfc"
    patcher = ISSPatcher(cache_dir=tmp_path / "cache")
    image = patch(patcher, rom, out, mapped(patcher, squad(), slot=1))
    assert image[0x38348 : 0x38348 + 44] == ITALY_433
    # Italy's label, "Offense 4-4-2" at 0x1209A, now ends in the new shape.
    assert retail[0x1209A : 0x1209A + 18] == b"\xfe\x2cOffense 4-4-2  \xfe"
    assert image[0x1209A : 0x1209A + 18] == b"\xfe\x2cOffense 4-3-3  \xfe"
    assert image[0x120A4 : 0x120A4 + 5] == b"4-3-3"
    assert image[0x3831C:0x38348] == retail[0x3831C:0x38348]  # Germany's record is Germany's
    assert names_of(out, 1)[8:11] == ["LW10", "RW8", "ST9"]


@real_rom
def test_the_galo_in_germanys_slot_of_the_cartridge_is_the_one_the_emulator_checked(
    retail, tmp_path
):
    """Task 15's second cartridge (`t15-galo-433-roleorder-in-germany.sfc`, checked in Mesen 2):
    the Galo in Germany's slot, 4-3-3 and the label of Germany's description block with it."""
    rom = tmp_path / "iss.sfc"
    rom.write_bytes(retail)
    patcher = ISSPatcher(cache_dir=tmp_path / "cache")
    for fetched_with_roles in (False, True):
        suggested = patched_galo(
            patcher, rom, tmp_path / "s.sfc", fetched_with_roles=fetched_with_roles,
            as_suggested=True,
        )  # fmt: skip
        automatic = patched_galo(
            patcher, rom, tmp_path / "a.sfc", fetched_with_roles=fetched_with_roles,
            as_suggested=False,
        )  # fmt: skip
        assert suggested == automatic
        assert suggested["formation"] == FORMATION_DONORS["4-3-3"] == ITALY_433
        assert suggested["label"] == b"\xfe\x24Defense 4-3-3  \xfe\x20\xfd"
        assert names_of(tmp_path / "s.sfc", 0)[:11] == GALO_ON_THE_CARTRIDGE
