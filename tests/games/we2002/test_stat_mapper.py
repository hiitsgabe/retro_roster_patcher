"""The two places `StatMapper` turns provider data into something the ROM stores.

`_format_player_name` is the one place a provider string becomes ROM bytes
without passing through a numeric mapping. Its three fallbacks -- display name to
last name, and forename to the display name's first word -- were each reached by
testing a string for truthiness, which a string of spaces passes.

The second half is the rating path: each ability ranks the player's stats against
his group in the league fetched onto the disc's own spread of it, and an ability
whose stats a provider did not supply (`PlayerStats.unsupplied`) is the group's
median on the disc rather than the floor.
"""

from dataclasses import replace

import pytest

from retro_roster_patcher.games.we2002.stat_mapper import (
    DISC_SPREAD,
    League,
    StatMapper,
    on_spread,
    preferred_foot,
)
from retro_roster_patcher.sports.models import Player, PlayerStats, Team, TeamRoster


@pytest.fixture
def mapper():
    return StatMapper()


def _name(mapper, name, last_name="", first_name=""):
    return mapper._format_player_name(
        Player(
            id=1,
            name=name,
            last_name=last_name,
            first_name=first_name,
            position="Midfielder",
        )
    )


def test_an_absent_display_name_falls_back_to_the_last_name(mapper):
    # The branch that already worked, and the baseline the whitespace cases are
    # measured against: they have to reach exactly this.
    assert _name(mapper, "", last_name="Silva") == ("Silva", "")


def test_an_empty_display_name_and_an_empty_last_name_give_two_empty_strings(mapper):
    assert _name(mapper, "", last_name="") == ("", "")


@pytest.mark.parametrize("blank", [" ", "  ", "\t\n", "   \t "])
def test_a_whitespace_display_name_is_treated_as_absent(mapper, blank):
    # `display` was tested for truthiness, and a string of spaces is truthy, so
    # the last-name fallback above was skipped. `display.split()` then returned
    # an empty list and `words[-1]` raised `IndexError` straight out of
    # `map_rosters`, aborting the entire patch for one malformed player.
    assert _name(mapper, blank, last_name="") == ("", "")


def test_a_whitespace_display_name_still_reaches_the_surname_beside_it(mapper):
    # The case that makes this more than a crash: the correct answer was sitting
    # in `last_name` the whole time and was unreachable. A fix that merely stops
    # the exception and returns `("", "")` here is not a fix — the player would
    # reach the ROM nameless.
    assert _name(mapper, "  ", last_name="Silva") == ("Silva", "")


def test_a_whitespace_display_name_keeps_the_forename_too(mapper):
    # Both fields come through the fallback branch, not just the surname.
    assert _name(mapper, "  ", last_name="Silva", first_name="Ana") == ("Silva", "Ana")


def test_a_whitespace_last_name_is_also_treated_as_absent(mapper):
    # Same defect, one fallback further in: `(last or "")` tested a string of
    # spaces for truthiness, so two spaces were returned as the player's ROM
    # surname instead of the empty string.
    assert _name(mapper, "", last_name="  ") == ("", "")
    assert _name(mapper, "", last_name="  ", first_name="Ana") == ("", "Ana")


def test_a_one_word_non_latin_name_falls_back_to_the_last_name(mapper):
    # `_to_ascii` drops every character it cannot render, so a name in a
    # non-Latin script comes back empty and the fallback was always reached.
    # This case never crashed; it is the contrast for the one below.
    assert _name(mapper, "Иванов", last_name="Ivanov") == ("Ivanov", "")
    assert _name(mapper, "김민재", last_name="Kim") == ("Kim", "")


@pytest.mark.parametrize(
    ("display", "surname"),
    [("Иванов Петров", "Petrov"), ("محمد صلاح", "Salah"), ("김 민재", "Kim")],
)
def test_a_multi_word_non_latin_name_falls_back_too(mapper, display, surname):
    # And this is what makes the whitespace bug ordinary rather than exotic. The
    # letters are dropped and the separating space is not, so a two-word name in
    # Cyrillic, Arabic or Hangul arrives at the truthiness test as `" "` — the
    # crash input, from a completely well-formed provider payload. No malformed
    # data is needed to reach it, only a league that is not written in Latin
    # script, and the Latin surname beside it was unreachable.
    assert _name(mapper, display, last_name=surname) == (surname, "")


def test_a_multi_word_non_latin_name_with_no_latin_fallback_is_empty_not_a_crash(mapper):
    assert _name(mapper, "Иванов Петров", last_name="") == ("", "")


def test_the_strip_happens_after_the_ascii_conversion_and_not_before(mapper):
    # The order is load-bearing and is not interchangeable: `"Иванов Петров"` is
    # not whitespace, so stripping the provider string first changes nothing and
    # `_to_ascii` still yields `" "`. Only stripping the converted string sees
    # it. A non-breaking space is the same shape — `_to_ascii` turns it into a
    # plain one.
    assert _name(mapper, "\xa0Neymar\xa0") == ("Neymar", "")
    assert _name(mapper, "Иванов Петров", last_name="Petrov") == ("Petrov", "")


def test_a_padded_mononym_loses_its_padding(mapper):
    # The mononym branch returned `display[:8]`, not `words[0]`, so a padded
    # name was truncated with its padding still attached: `"  Neymar  "[:8]`
    # is `"  Neyma"`. Eight characters is the ROM's whole budget, so two of them
    # were spaces.
    assert _name(mapper, "  Neymar  ") == ("Neymar", "")


def test_a_padded_two_word_name_loses_its_padding(mapper):
    assert _name(mapper, "  Victor Hugo  ") == ("V. Hugo", "Victor")


def test_a_whitespace_forename_falls_back_to_the_first_word(mapper):
    # The third fallback, `(first or words[0])`, tested for truthiness in the
    # same way. With a forename of spaces the fallback was skipped and the
    # spaces were returned in place of "Victor".
    assert _name(mapper, "Victor Hugo", first_name="  ") == ("V. Hugo", "Victor")


def test_a_supplied_forename_still_beats_the_first_word(mapper):
    # The fallback only fires when there is nothing to fall back from: a real
    # forename must not be replaced by the display name's first word.
    assert _name(mapper, "Victor Hugo", first_name="Vitinho") == ("V. Hugo", "Vitinho")


def test_a_mononym_is_used_as_it_stands(mapper):
    assert _name(mapper, "HULK") == ("HULK", "")
    assert _name(mapper, "Neymar") == ("Neymar", "")


def test_a_two_word_name_becomes_an_initial_and_a_surname(mapper):
    assert _name(mapper, "Victor Hugo") == ("V. Hugo", "Victor")
    assert _name(mapper, "Gerard Pique") == ("G. Pique", "Gerard")


def test_a_three_word_name_uses_the_first_and_last_words(mapper):
    assert _name(mapper, "Luis Carlos Veiga") == ("L. Veiga", "Luis")


def test_the_rom_budget_of_eight_characters_is_applied_to_both_fields(mapper):
    # Every return path truncates, and 8 is the whole budget the game displays.
    assert _name(mapper, "Cunningham") == ("Cunningh", "")
    assert _name(mapper, "Alexander Oxlade") == ("A. Oxlad", "Alexande")
    assert _name(mapper, "", last_name="Vandenberghe") == ("Vandenbe", "")


def test_a_name_that_is_only_punctuation_is_not_whitespace_and_is_kept(mapper):
    # `.strip()` removes whitespace and nothing else, so a name the provider
    # really sent — however odd — still reaches the ROM rather than being
    # discarded as blank.
    assert _name(mapper, "-") == ("-", "")


def test_a_whitespace_named_player_no_longer_aborts_the_whole_team(mapper):
    # `map_team_with_league_context` is what `map_rosters` calls, and the
    # `IndexError` left it uncaught: one malformed player in one team killed the
    # entire patch run. Three players, so the two good ones are visibly retained
    # rather than the team collapsing to nothing.
    squad = [
        Player(id=1, name="Neymar", position="Attacker"),
        Player(id=2, name="  ", last_name="Silva", position="Defender"),
        Player(id=3, name="Victor Hugo", position="Midfielder"),
    ]
    roster = TeamRoster(team=Team(id=100, name="Utd", code="UTD"), players=squad)

    record = mapper.map_team_with_league_context(roster, [roster])

    # Sorted, because `_select_best_23` orders by position and rating and this
    # test is about none of the three being lost, not about their order.
    assert sorted(p.last_name for p in record.players) == ["Neymar", "Silva", "V. Hugo"]
    assert len(record.players) == 3


# The four `PlayerStats` fields ESPN's soccer statistics document has no counterpart for.
ABSENT_FROM_ESPN = ("duels_total", "duels_won", "dribbles_attempts", "dribbles_success")

_BASE_STATS = PlayerStats(
    player_id=0,
    appearances=20,
    minutes=1600,
    goals=3,
    assists=2,
    shots_total=20,
    shots_on=8,
    passes_total=800,
    passes_accuracy=80.0,
    tackles_total=20,
    interceptions=10,
    blocks=5,
    duels_total=0,
    duels_won=0,
    dribbles_attempts=0,
    dribbles_success=0,
    fouls_committed=10,
    fouls_drawn=8,
    cards_yellow=2,
    cards_red=0,
    rating=None,
    lineups=18,
)

MEDIAN = {
    group: {ability: on_spread(0.5, counts) + 1 for ability, counts in spread.items()}
    for group, spread in DISC_SPREAD.items()
}


def _rate(players, stats):
    """Every player's abilities, ranked against the league of `players`."""
    roster = TeamRoster(team=Team(id=1, name="L"), players=players, player_stats=stats)
    league = League([roster])
    return [StatMapper().map_player(p, stats.get(p.id), league) for p in players]


def _forwards(n, **per_player):
    """`n` forwards, the k-th with `per_player[field](k)` of each field given."""
    players = [Player(id=k, name=f"F{k}", position="Attacker") for k in range(n)]
    stats = {
        k: replace(
            _BASE_STATS,
            player_id=k,
            unsupplied=ABSENT_FROM_ESPN,
            **{f: fn(k) for f, fn in per_player.items()},
        )
        for k in range(n)
    }
    return players, stats


def test_a_league_as_big_as_the_disc_takes_exactly_the_discs_spread():
    # 317 forwards with 317 different goal counts: their offensive values are the disc's 317
    # forwards', value for value.
    players, stats = _forwards(317, goals=lambda k: k)
    rated = _rate(players, stats)
    counts = [0] * 8
    for attrs in rated:
        counts[attrs.offensive - 1] += 1
    assert tuple(counts) == DISC_SPREAD["Attacker"]["offensive"]


def test_the_league_s_best_scorer_takes_the_disc_s_best_forward_value():
    players, stats = _forwards(40, goals=lambda k: k)
    rated = _rate(players, stats)
    assert rated[-1].offensive == 8 and rated[0].offensive == 5


def test_espn_s_missing_duels_and_dribbles_no_longer_floor_the_squad():
    # Fouls drawn stand in for both, pass accuracy for technique.
    players, stats = _forwards(30, fouls_drawn=lambda k: k, passes_accuracy=lambda k: 60.0 + k)
    rated = _rate(players, stats)
    for ability in ("body_balance", "dribble", "technique"):
        values = [getattr(a, ability) for a in rated]
        assert len(set(values)) >= 3, ability
        assert values[-1] > values[0], ability


def test_a_stat_the_provider_did_not_supply_gives_the_group_median_not_the_floor():
    # A line with goals, assists and shots on target alone: offensive and speed (which ranks
    # attacking output) rank, and every other ability is the median, not the floor.
    kept = {"goals", "assists", "shots_on", "appearances"}
    unsupplied = tuple(
        f for f, kind in PlayerStats.__annotations__.items() if kind == "int" and f not in kept
    ) + ("passes_accuracy",)
    players = [Player(id=k, name=f"M{k}", position="Midfielder") for k in range(30)]
    stats = {
        k: replace(_BASE_STATS, player_id=k, goals=k, unsupplied=unsupplied) for k in range(30)
    }
    rated = _rate(players, stats)
    assert rated[-1].offensive > rated[0].offensive
    for attrs in rated:
        for ability, median in MEDIAN["Midfielder"].items():
            if ability not in ("offensive", "speed"):
                assert getattr(attrs, ability) == median, ability


def test_a_player_without_stats_takes_his_group_s_medians_but_for_his_bio():
    keeper = Player(id=1, name="K", position="Goalkeeper")
    (attrs,) = _rate([keeper], {})
    assert vars(attrs) == MEDIAN["Goalkeeper"]


def test_an_unknown_position_ranks_with_the_midfielders():
    (attrs,) = _rate([Player(id=1, name="X", position="Sweeper")], {})
    assert vars(attrs) == MEDIAN["Midfielder"]


def test_a_keeper_s_reflexes_come_from_his_save_rate_not_from_his_defence():
    # Ten keepers who concede the same and save more and more: one defence, rising reflexes.
    keepers = [Player(id=k, name=f"K{k}", position="Goalkeeper") for k in range(10)]
    stats = {
        k: replace(_BASE_STATS, player_id=k, saves=60 + 5 * k, goals_conceded=40) for k in range(10)
    }
    rated = _rate(keepers, stats)
    reflexes = [a.reflexes for a in rated]
    assert reflexes == sorted(reflexes) and reflexes[-1] > reflexes[0]
    assert len({a.defensive for a in rated}) == 1


def test_younger_players_are_faster_and_shorter_ones_quicker_off_the_mark():
    players = [
        Player(id=k, name=f"D{k}", position="Defender", age=36 - 2 * k, height=78 - k)
        for k in range(8)
    ]
    rated = _rate(players, {})
    speed = [a.speed for a in rated]
    acceleration = [a.acceleration for a in rated]
    assert speed == sorted(speed) and speed[-1] > speed[0]
    assert acceleration == sorted(acceleration) and acceleration[-1] > acceleration[0]


def test_heading_ranks_headed_goals_and_jump_height():
    players, stats = _forwards(20, headed_goals=lambda k: k // 2)
    for k, player in enumerate(players):
        player.height = 170 + k
    rated = _rate(players, stats)
    assert rated[-1].heading > rated[0].heading
    assert rated[-1].jump_power > rated[0].jump_power


def test_curve_ranks_crosses_and_free_kicks():
    players, stats = _forwards(
        20,
        crosses_total=lambda k: 5 * k,
        crosses_accurate=lambda k: k,
        free_kick_goals=lambda k: k // 5,
    )
    rated = _rate(players, stats)
    assert rated[-1].curve > rated[0].curve


@pytest.mark.parametrize(
    ("left", "right", "unsupplied", "foot"),
    [
        (2, 30, (), 0),
        (25, 5, (), 1),
        (12, 14, (), 2),
        (5, 3, (), 0),  # too few shots to say
        (25, 5, ("left_foot_shots",), 0),
    ],
)
def test_the_preferred_foot_comes_from_the_shots_he_took_with_each(left, right, unsupplied, foot):
    stats = replace(
        _BASE_STATS, left_foot_shots=left, right_foot_shots=right, unsupplied=unsupplied
    )
    assert preferred_foot(stats) == foot
    assert preferred_foot(None) == 0


def test_the_mapped_record_carries_the_foot():
    player = Player(id=1, name="Lefty", position="Attacker")
    stats = {1: replace(_BASE_STATS, player_id=1, left_foot_shots=30, right_foot_shots=3)}
    roster = TeamRoster(team=Team(id=1, name="L"), players=[player], player_stats=stats)
    (record,) = StatMapper().map_team_with_league_context(roster, [roster]).players
    assert record.foot == 1


def test_the_spreads_are_the_discs_own(disc):
    from retro_roster_patcher.games.we2002 import rom_writer

    groups = ["Goalkeeper", "Defender", "Defender"] + ["Midfielder"] * 3 + ["Attacker"] * 2
    fields = {  # (bit offset in the 96-bit little-endian record, width)
        "offensive": (61, 3), "defensive": (64, 3), "body_balance": (46, 3), "stamina": (49, 3),
        "speed": (55, 3), "acceleration": (58, 3), "pass_accuracy": (73, 3),
        "shoot_power": (67, 3), "shoot_accuracy": (70, 3), "jump_power": (82, 3),
        "heading": (79, 3), "technique": (76, 3), "dribble": (52, 3), "curve": (85, 3),
        "aggression": (88, 3), "reflexes": (42, 3),
    }  # fmt: skip
    counts = {g: {a: [0] * 8 for a in fields} for g in DISC_SPREAD}
    for place in range(63 * 23):
        record = b"".join(disc(o, n) for o, n in rom_writer._nat_carat_chunks(place))
        bits = int.from_bytes(record, "little")
        group = groups[bits & 7]
        for ability, (at, width) in fields.items():
            counts[group][ability][(bits >> at) & ((1 << width) - 1)] += 1
    assert {g: {a: tuple(c) for a, c in v.items()} for g, v in counts.items()} == DISC_SPREAD


# The squad order: the first 11 slots are a 4-4-2 (keeper, four defenders, four
# midfielders, two forwards), then the bench.


def _squad(**counts):
    """Players numbered from 0 in group order: `_squad(Goalkeeper=3, ...)`."""
    groups = [g for g, n in counts.items() for _ in range(n)]
    return [Player(id=i, name=f"P{i:02d}", position=g) for i, g in enumerate(groups)]


def _starts(players, **lineups):
    """Season stats with `lineups` falling with the id, so low ids are regulars."""
    return {
        p.id: replace(_BASE_STATS, player_id=p.id, lineups=lineups.get(p.name, 40 - p.id))
        for p in players
    }


def _lineup(*starters):
    """A `get_lineup` result: (player id, ESPN position code), most starts first."""
    return {
        "formation": "",
        "event": 1,
        "starters": [{"id": i, "position": c} for i, c in starters],
    }


def _groups(players):
    return [p.position[0] for p in players]


def _lines(order):
    """The keeper and the sets of ids in the 4-4-2's defence, midfield and attack."""
    return (
        order[0].id,
        {p.id for p in order[1:5]},
        {p.id for p in order[5:9]},
        {p.id for p in order[9:11]},
    )


SEASON = _squad(Goalkeeper=3, Defender=9, Midfielder=8, Attacker=7)


def test_without_a_lineup_the_season_regulars_start(mapper):
    order = mapper._select_best_23(SEASON, _starts(SEASON))
    assert [p.id for p in order[:11]] == [0, 3, 4, 5, 6, 12, 13, 14, 15, 20, 21]
    # The bench takes one of each group in turn, so a Master League slot's few
    # subs are a keeper and an outfielder of each line.
    assert _groups(order[11:15]) == ["G", "D", "M", "A"]
    assert sorted(_groups(order)) == sorted("G" * 3 + "D" * 7 + "M" * 6 + "A" * 6 + "D")
    assert len(order) == 23


def test_every_recent_starter_starts_in_his_lineup_line(mapper):
    # The season's regulars are the low ids; the recent eleven mostly is not.
    lineup = _lineup(
        (2, "G"),
        (11, "RB"),
        (10, "CD"),
        (9, "CD"),
        (8, "LB"),
        (19, "DM"),
        (18, "CM"),
        (17, "AM"),
        (16, "LM"),
        (25, "CF"),
        (24, "CF"),
    )
    order = mapper._select_best_23(SEASON, _starts(SEASON), lineup)
    assert _lines(order) == (2, {8, 9, 10, 11}, {16, 17, 18, 19}, {24, 25})
    # Back to front within a line too: centre-backs, then full-backs.
    assert [p.id for p in order[1:5]] == [10, 9, 8, 11]
    # The regulars he displaced lead the bench.
    assert [p.id for p in order[11:15]] == [0, 3, 12, 20]


def test_a_front_three_sends_its_widest_forward_to_midfield(mapper):
    # A 4-3-3: a winger takes the fourth midfield place, ahead of every
    # midfielder who did not start.
    lineup = _lineup(
        (0, "G"),
        (3, "RB"),
        (4, "CD"),
        (5, "CD"),
        (6, "LB"),
        (12, "DM"),
        (13, "CM"),
        (14, "CM"),
        (20, "LW"),
        (21, "CF"),
        (22, "RW"),
    )
    order = mapper._select_best_23(SEASON, _starts(SEASON), lineup)
    assert _lines(order) == (0, {3, 4, 5, 6}, {12, 13, 14, 20}, {21, 22})


def test_a_back_three_takes_a_midfielder_and_midfield_a_forward(mapper):
    # A 3-4-2-1 as ESPN codes it: the wing-backs are LM/RM and the two behind
    # the striker CF-L/CF-R. Every line moves back one, so no forward ends
    # up in defence.
    lineup = _lineup(
        (0, "G"),
        (4, "CD-L"),
        (5, "CD"),
        (6, "CD"),
        (12, "RM"),
        (13, "LM"),
        (14, "CM-R"),
        (15, "CM-L"),
        (20, "F"),
        (21, "CF-R"),
        (22, "CF-L"),
    )
    order = mapper._select_best_23(SEASON, _starts(SEASON), lineup)
    assert _lines(order) == (0, {4, 5, 6, 13}, {12, 14, 15, 22}, {20, 21})


def test_an_eleven_of_six_midfielders_still_gets_a_midfield_sub(mapper):
    # A 4-2-3-1 as ESPN often codes it: the pivots LM/RM and four AMs. The
    # bench counts the 4-4-2's places, not the eleven's groups, so the four
    # subs a Master League slot keeps still cover every line.
    lineup = _lineup(
        (0, "G"),
        (3, "CD-L"),
        (4, "CD-R"),
        (5, "RB"),
        (12, "LM"),
        (13, "RM"),
        (14, "AM-L"),
        (15, "AM"),
        (16, "AM-R"),
        (17, "AM"),
        (20, "F"),
    )
    order = mapper._select_best_23(SEASON, _starts(SEASON), lineup)
    assert _groups(order[11:15]) == ["G", "D", "M", "A"]


def test_a_line_the_squad_cannot_fill_takes_an_outfielder_not_a_keeper(mapper):
    order = mapper._select_best_23(_squad(Goalkeeper=3, Defender=3, Midfielder=8, Attacker=6))
    assert _groups(order[:11]) == list("GDDDMMMMMAA")


def test_a_midfielder_who_started_up_front_plays_up_front(mapper):
    # The lineup group decides, not the provider's position.
    lineup = _lineup((0, "G"), (12, "LF"))
    order = mapper._select_best_23(SEASON, _starts(SEASON), lineup)
    assert [p.id for p in order[9:11]] == [12, 20]
    assert 12 not in [p.id for p in order[5:9]]


def test_a_second_keeper_in_the_lineup_sits_first_on_the_bench(mapper):
    lineup = _lineup((1, "G"), (2, "G"), (3, "CD"))
    order = mapper._select_best_23(SEASON, _starts(SEASON), lineup)
    assert [p.id for p in order[:2]] == [1, 3]
    assert order[11].id == 2


def test_a_starter_who_left_the_club_leaves_his_place_to_a_regular(mapper):
    lineup = _lineup((0, "G"), (99, "CD"), (8, "CD"))
    order = mapper._select_best_23(SEASON, _starts(SEASON), lineup)
    assert [p.id for p in order[1:5]] == [8, 3, 4, 5]


def test_the_lineup_reaches_the_record_the_writer_gets(mapper):
    lineup = _lineup((2, "G"), (11, "CD"))
    roster = TeamRoster(
        team=Team(id=100, name="Utd", code="UTD"),
        players=SEASON,
        player_stats=_starts(SEASON),
        extra={"lineup": lineup},
    )
    record = mapper.map_team_with_league_context(roster, [roster])
    assert [p.last_name for p in record.players[:2]] == ["P02", "P11"]


def test_a_mapped_record_carries_the_players_bio_for_his_look(mapper):
    player = Player(
        id=7,
        name="Pedro Silva",
        position="Midfielder",
        photo_url="p",
        nationality="Brazil",
        height=70.9,
        weight=160.0,
        age=29,
    )
    roster = TeamRoster(team=Team(id=100, name="Team", code="TEA"), players=[player])
    (record,) = mapper.map_team_with_league_context(roster, [roster]).players
    assert (record.player_id, record.photo_url, record.nationality) == (7, "p", "Brazil")
    assert (record.height_cm, record.age) == (180, 29)
