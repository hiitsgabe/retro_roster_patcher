"""`team_slots`: how many slots one league fills, which the app caps its mapping at."""

from __future__ import annotations

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster

CASES = [("iss-snes", 26), ("iss-deluxe-snes", 36), ("iss2000-n64", 100), ("we2002", 63)]


def test_the_values_are_pinned():
    assert {g: get_patcher(g).team_slots for g, _ in CASES} == dict(CASES)
    assert get_patcher("pes2013-ps2").team_slots is None


@pytest.mark.parametrize(("game", "n"), CASES)
def test_the_last_slot_a_league_fills_maps(tmp_path, game, n):
    patcher = get_patcher(game)(tmp_path)
    order = patcher.slot_order if getattr(patcher, "slot_order", ()) else range(n)
    slot = list(order)[n - 1]
    groups = ["Goalkeeper"] * 2 + ["Defender"] * 6 + ["Midfielder"] * 6 + ["Attacker"] * 6
    roster = TeamRoster(
        team=Team(id=1, name="T"),
        players=[Player(id=i, name=f"P{i}", position=g) for i, g in enumerate(groups, start=1)],
    )
    data = LeagueData(league=League(id=2001, name="L"), teams=[roster])
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=slot, team_id=1)])
    assert set(mapped.teams) == {slot}
