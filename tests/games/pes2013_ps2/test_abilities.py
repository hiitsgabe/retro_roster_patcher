"""PES 2013 (PS2): the ability fields of a player record, and the ratings a season sets."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from retro_roster_patcher.games.pes2013_ps2.abilities import (
    FIELDS,
    HEIGHT_BASE,
    SIDE_BOTH,
    SIDE_OTHER,
    SIDE_OWN,
    LeagueStats,
    defaults,
    on_disc,
    read,
    spreads,
    write,
)
from retro_roster_patcher.games.pes2013_ps2.models import PLAYER_ENTRIES
from retro_roster_patcher.games.pes2013_ps2.patcher import position_group
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader
from retro_roster_patcher.sports.models import Player, PlayerStats

#: Bytes 48-91 of the retail disc's Cristiano Ronaldo (record 447) and Casillas (599).
RONALDO = bytes(48) + bytes.fromhex(
    "5d020000b4b1631e5a4e575c55d7e1e1d3d3575cdce0545d555f59485a5fca3264b4000000a7571427d098a9"
)
CASILLAS = bytes(48) + bytes.fromhex(
    "2c00000001009e62513c4b4e614f38393a3b3b402d4b2d2d2d37554e2f3d5b5f9c9ec00800079f0025cf18bd"
)


def test_the_retail_records_read_as_their_pes_2013_ratings():
    """pesmaster's PES 2013 Ronaldo: attack 99, defence 30, header 95, kicking power 96, place
    kicking 93, ball control 90, 187 cm, 80 kg, right foot, weak foot 7 / 5, form 5, injury B;
    roulette, turning and flip flap. Casillas: goalkeeping 95, both stopper flags."""
    cr = read(RONALDO)
    assert (cr["attack"], cr["defence"], cr["heading"], cr["shot_power"]) == (99, 30, 95, 96)
    assert (cr["free_kick_accuracy"], cr["technique"], cr["goalkeeping"]) == (93, 90, 50)
    assert (cr["height"] + HEIGHT_BASE, cr["weight"], cr["foot"]) == (187, 80, 0)
    assert (cr["weak_foot_accuracy"] + 1, cr["weak_foot_frequency"] + 1) == (7, 5)
    assert (cr["condition"] + 1, cr["injury_tolerance"]) == (5, 1)
    assert cr["roulette"] == cr["turning"] == cr["flip_flap"] == 1
    assert cr["one_touch_pass"] == cr["long_throw"] == 0
    gk = read(CASILLAS)
    assert (gk["goalkeeping"], gk["response"], gk["foot"]) == (95, 97, 1)
    assert gk["penalty_stopper"] == gk["one_on_one_stopper"] == 1


def test_writing_every_field_changes_no_bit_a_field_does_not_own():
    record = bytearray(CASILLAS)
    write(record, read(RONALDO))
    assert read(record) == read(RONALDO)
    # Bits no field owns stay: the playable flags (bit 7 of 54-65), byte 85 bits 3-4 and 6,
    # byte 87 bits 0 and 4, the call name, the shirt flag of byte 89, bytes 90-91.
    owned = bytearray(124)
    for at, shift, mask in FIELDS.values():
        owned[at] |= mask << shift
    assert all((record[i] ^ CASILLAS[i]) & ~owned[i] & 0xFF == 0 for i in range(48, 92)), (
        "a bit no field owns changed"
    )
    assert owned[85] == 0xA7 and owned[87] == 0x06 and owned[54] == 0x7F and owned[51] == 0


def test_a_value_is_clamped_to_its_field():
    record = bytearray(124)
    write(record, {"attack": 140, "consistency": 9, "height": -3})
    assert read(record)["attack"] == 127 and read(record)["consistency"] == 7
    assert read(record)["height"] == 0


def test_a_share_lands_on_the_disc_spread_and_the_defaults_are_its_median():
    counts = [(40, 1), (60, 2), (80, 1)]
    assert [on_disc(s, counts) for s in (0.0, 0.3, 0.6, 0.99, 1.0)] == [40, 60, 60, 80, 80]
    attack = spreads()["Attacker"]["attack"]
    assert defaults("Attacker")["attack"] == on_disc(0.5, attack)
    assert defaults("Attacker")["roulette"] == 0  # the disc's attackers mostly have none
    assert defaults("Nonsense") == defaults("Midfielder")


def _line(pid, minutes=2700, **counts):
    base = dict(
        player_id=pid,
        appearances=30,
        minutes=minutes,
        goals=0,
        assists=0,
        shots_total=0,
        shots_on=0,
        passes_total=0,
        passes_accuracy=0.0,
        tackles_total=0,
        interceptions=0,
        blocks=0,
        duels_total=0,
        duels_won=0,
        dribbles_attempts=0,
        dribbles_success=0,
        fouls_committed=0,
        fouls_drawn=0,
        cards_yellow=0,
        cards_red=0,
        rating=None,
        lineups=30,
        unsupplied=("duels_total", "duels_won", "dribbles_attempts", "dribbles_success"),
    )
    return PlayerStats(**{**base, **counts})


def _strikers():
    players = [Player(id=i, name=f"S{i}", age=25, position="Forward") for i in range(1, 6)]
    lines = [
        _line(i, goals=g, shots_total=40, shots_on=20)
        for i, g in zip(range(1, 6), (2, 5, 8, 12, 25), strict=True)
    ]
    return players, lines


def test_the_leagues_top_scorer_gets_the_discs_top_scoring_and_the_worst_its_lowest():
    players, lines = _strikers()
    league = LeagueStats.of((p, "Attacker", s) for p, s in zip(players, lines, strict=True))
    spread = spreads()["Attacker"]
    best = league.abilities(players[-1], "Attacker", lines[-1])
    worst = league.abilities(players[0], "Attacker", lines[0])
    assert best["scoring"] == 1 and worst["scoring"] == 0
    assert best["shot_technique"] == on_disc(0.9, spread["shot_technique"])
    assert worst["shot_technique"] == on_disc(0.1, spread["shot_technique"])
    assert best["shot_technique"] > worst["shot_technique"]


def test_a_stat_the_line_lacks_gives_the_default_not_zero():
    players, lines = _strikers()
    lines = [replace(s, unsupplied=(*s.unsupplied, "tackles_won", "interceptions",
                                    "clearances", "blocks")) for s in lines]  # fmt: skip
    league = LeagueStats.of((p, "Attacker", s) for p, s in zip(players, lines, strict=True))
    own = league.abilities(players[2], "Attacker", lines[2])
    assert "defence" not in own and "marking" not in own  # the patcher gives the default
    assert own["shot_technique"] > 0 and own["shot_accuracy"] > 0


def test_a_player_without_a_line_is_set_by_his_body_and_role_alone():
    tall = Player(id=9, name="T", age=30, height=76.0, weight=190.0, position="Defender")
    league = LeagueStats.of([(tall, "Defender", None)])
    own = league.abilities(tall, "Defender", None, "CB")
    assert own["height"] + HEIGHT_BASE == 193 and own["weight"] == 86
    assert own["favoured_side"] == SIDE_BOTH
    assert set(own) <= {"height", "weight", "favoured_side", "jump", "balance", "top_speed",
                        "acceleration", "agility", "mentality", "response", "dribble_speed",
                        "post_player", "heading", "fame"}  # fmt: skip


def test_the_foot_is_the_one_he_shoots_with_and_the_side_his_roles():
    p = Player(id=1, name="L", age=24, position="Defender")
    league = LeagueStats.of([(p, "Defender", None)])
    lefty = _line(1, left_foot_shots=9, right_foot_shots=2)
    assert league.abilities(p, "Defender", lefty, "LB")["foot"] == 1
    assert league.abilities(p, "Defender", lefty, "LB")["favoured_side"] == SIDE_OWN
    assert league.abilities(p, "Defender", lefty, "RB")["favoured_side"] == SIDE_OTHER
    few = _line(1, left_foot_shots=1, right_foot_shots=0)
    assert "foot" not in league.abilities(p, "Defender", few, "LB")


@pytest.fixture
def disc():
    """The retail image's path."""
    root = os.environ.get("RETRO_ROSTER_TEST_ROMS")
    if not root:
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding pes2013.iso")
    path = Path(root) / "pes2013.iso"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path


@pytest.mark.real_rom
def test_on_the_disc_the_spreads_are_its_club_and_national_players_by_group(disc):
    reader = RomReader(str(disc))
    assert reader.load()
    t = reader.player_tables(PLAYER_ENTRIES[0])
    assert t.record(447)[48:92] == RONALDO[48:] and t.record(599)[48:92] == CASILLAS[48:]
    counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for i in t.referenced():
        if 0 < i < t.player_count:
            record = t.record(i)
            for name, value in read(record).items():
                counts[position_group(record)][name][value] += 1
    assert {g: {f: sorted(c.items()) for f, c in fs.items()} for g, fs in counts.items()} == (
        spreads()
    )


def test_a_foot_he_shoots_with_little_more_than_the_other_says_nothing():
    p = Player(id=1, name="B", age=23, position="Midfielder")
    league = LeagueStats.of([(p, "Midfielder", None)])
    both = _line(1, left_foot_shots=12, right_foot_shots=9)
    assert "foot" not in league.abilities(p, "Midfielder", both, "CAM")


def test_keepers_rank_on_saves_over_shots_on_goal_not_on_how_busy_they_are():
    """ESPN's shots faced is 0: the save rate is saves over saves and goals conceded."""
    busy = Player(id=1, name="Busy", age=28, position="Goalkeeper")
    sure = Player(id=2, name="Sure", age=28, position="Goalkeeper")
    lines = {
        1: _line(1, saves=90, goals_conceded=45, clean_sheets=3),
        2: _line(2, saves=40, goals_conceded=8, clean_sheets=12),
    }
    league = LeagueStats.of([(busy, "Goalkeeper", lines[1]), (sure, "Goalkeeper", lines[2])])
    sure_gk = league.abilities(sure, "Goalkeeper", lines[2])["goalkeeping"]
    assert sure_gk > league.abilities(busy, "Goalkeeper", lines[1])["goalkeeping"]
