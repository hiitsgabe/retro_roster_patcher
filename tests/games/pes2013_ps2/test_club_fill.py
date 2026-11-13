"""PES 2013 (PS2): the club fill's slots, regions and own slots, and a fill club's league.

The fill takes the clubs of no league group first (98-125, the Champions League's "Other
leagues A/B"), then the groups no league takes, then the Exhibition-only sides 126-129; never
the hidden Master League sides 130-138. A club the disc has goes back to its own slot, and a
club of an unused group's country into that group. A fill club never renames a group.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.pes2013_ps2 import afs
from retro_roster_patcher.games.pes2013_ps2.patcher import DISC_CLUBS, PES2013PS2Patcher
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader
from retro_roster_patcher.sports.clubs import CLUB_FILL_LEAGUE
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_pes2013_iso as fixture

ROM = None  # the hooks read nothing off the disc


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / "pes.iso"
    fixture.write_iso(path)
    return path


def test_the_fill_takes_the_other_clubs_then_the_unused_groups_then_126_to_129(tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    assert patcher.supports_club_fill
    english = set(range(20))
    assert patcher.club_fill_slots(ROM, english) == [
        *range(98, 126),
        *range(20, 98),
        *range(126, 130),
    ]
    # One slot of a group is enough to keep the whole group out; taken slots are left out.
    assert patcher.club_fill_slots(ROM, {5, 25, 45, 65, 85, 98, 126}) == [
        *range(99, 126),
        *range(127, 130),
    ]
    assert max(patcher.club_fill_slots(ROM, set())) == 129


def test_a_disc_club_has_its_own_slot_and_each_group_its_country(tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    assert patcher.own_club_slot(ROM, Team(id=132, name="Bayern Munich")) == 104
    assert patcher.own_club_slot(ROM, Team(id=83, name="Barcelona")) == 80
    assert patcher.own_club_slot(ROM, Team(id=1, name="Nowhere FC")) is None
    regions = patcher.club_fill_regions(ROM)
    assert (regions[0], regions[20], regions[59], regions[77], regions[97]) == (
        "ENG",
        "FRA",
        "ITA",
        "NED",
        "ESP",
    )
    assert 98 not in regions


def test_the_disc_club_table_holds_one_slot_per_club_below_126():
    assert all(slot < 126 and (slot >= 20 or slot == 7) for slot in DISC_CLUBS)
    assert len(set(DISC_CLUBS.values())) == len(DISC_CLUBS)


def _club(team_id, name):
    return TeamRoster(
        team=Team(id=team_id, name=name, code=name[:3].upper()),
        players=[
            Player(id=team_id * 10, name="A Keeper", last_name="Keeper", position="Goalkeeper")
        ],
    )


def test_a_fill_club_names_no_group_after_its_league(iso, tmp_path):
    league = LeagueData(
        league=League(id=1, name="Fixture League", season=2026), teams=[_club(10, "Arsenal")]
    )
    fill = LeagueData(
        league=League(id=CLUB_FILL_LEAGUE, name="Europe", season=2026),
        teams=[_club(160, "Paris Saint-Germain")],
    )
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(league, [SlotMapping(slot_index=0, team_id=10)]).merge(
        patcher.map_rosters(fill, [SlotMapping(slot_index=20, team_id=160)])
    )
    assert rosters.teams[20].league_name == "" and rosters.teams[0].league_name
    out = tmp_path / "out.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False)
    reader = RomReader(str(out))
    assert reader.load()
    table = afs.Table.parse(afs.unwrap(reader.read("U_TEXT.AFS", 15))[1])
    names = afs.unwrap(table.parts[0])[1].split(b"\0")
    assert b"FIXTURE LEAGUE" in names and b"Ligue 1" in names and b"EUROPE" not in names


def test_blank_unused_can_blank_one_kind_only(iso, tmp_path):
    from retro_roster_patcher.games.pes2013_ps2.models import (
        DATABASE_OVERLAY_ENGLISH,
        NATIONAL_SLOT_BASE,
    )

    clubs = LeagueData(league=League(id=1, name="L", season=2026), teams=[_club(10, "Arsenal")])
    nations = LeagueData(
        league=League(id=2021, name="N", season=2026), teams=[_club(20, "Nation 0")]
    )
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(clubs, [SlotMapping(slot_index=0, team_id=10)]).merge(
        patcher.map_rosters(nations, [SlotMapping(slot_index=NATIONAL_SLOT_BASE, team_id=20)])
    )
    out = tmp_path / "out.iso"
    patcher.patch(
        rom_path=iso, output_path=out, rosters=rosters, logos=False, blank_unused="nations"
    )
    teams = RomReader(str(out))
    assert teams.load()
    teams = teams.overlay_teams(DATABASE_OVERLAY_ENGLISH)
    assert teams.clubs[1][0].text == "WEST MIDLANDS VILLAGE"  # clubs kept
    assert teams.nationals[1][0].text == ""  # nations blanked
    with pytest.raises(Exception, match="blank_unused takes"):
        patcher.patch(rom_path=iso, output_path=out, rosters=rosters, blank_unused="kits")
