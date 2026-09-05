"""The ISS stat mapper: four attributes, three degrees of freedom, one gate.

`speed` and `stamina` come out of the *same* lambda, behind the same skip
predicate, and through the same table, so they are equal for every player the
provider measured -- and *not* for one who falls back to his position's defaults,
where all four rows separate them. Preserved deliberately: nothing ESPN reports
for a footballer measures pace.

`technique` is half dribbling and half passing, and ESPN measures no dribbling at
all. The gate is `CATEGORY_OPTIONAL_INPUTS`, and it is NOT WE2002's rule:
dropping the player from the category would leave it empty and give every player
the same rating, so the dribbling *term* is dropped instead, which preserves the
ranking exactly.
"""

from __future__ import annotations

import dataclasses

import pytest

from retro_roster_patcher.games.iss_snes.models import PLAYERS_PER_TEAM
from retro_roster_patcher.games.iss_snes.stat_mapper import ISSStatMapper
from retro_roster_patcher.sports.espn import SOCCER_UNSUPPLIED_STATS
from retro_roster_patcher.sports.models import Player, PlayerStats, Team, TeamRoster


@pytest.fixture
def mapper():
    return ISSStatMapper()


def _stats(player_id, **kwargs):
    base = dict(
        appearances=20,
        minutes=1600,
        goals=5,
        assists=3,
        shots_total=30,
        shots_on=12,
        passes_total=600,
        passes_accuracy=80.0,
        tackles_total=20,
        interceptions=15,
        blocks=5,
        duels_total=100,
        duels_won=55,
        dribbles_attempts=40,
        dribbles_success=20,
        fouls_committed=15,
        fouls_drawn=12,
        cards_yellow=3,
        cards_red=0,
        rating=None,
        lineups=18,
    )
    base.update(kwargs)
    return PlayerStats(player_id=player_id, **base)


def _player(player_id, position="Midfielder", **kwargs):
    return Player(
        id=player_id,
        name=kwargs.pop("name", f"Given Surname{player_id:02d}"),
        position=position,
        **kwargs,
    )


def _roster(players, stats):
    return TeamRoster(
        team=Team(id=1, name="Test United", code="TST"),
        players=players,
        player_stats={s.player_id: s for s in stats},
    )


def test_the_category_input_table_names_only_real_playerstats_fields():
    """The trap `CATEGORY_INPUTS` exists to close: a misspelt field name can
    never be found in `unsupplied`, so the gate silently stops gating."""
    fields = {f.name for f in dataclasses.fields(PlayerStats)}
    named = {name for names in ISSStatMapper.CATEGORY_INPUTS.values() for name in names}
    assert named <= fields


def test_the_optional_input_table_names_only_real_playerstats_fields():
    fields = {f.name for f in dataclasses.fields(PlayerStats)}
    named = {name for names in ISSStatMapper.CATEGORY_OPTIONAL_INPUTS.values() for name in names}
    assert named <= fields


def test_the_category_input_table_covers_exactly_the_computed_categories(mapper):
    computed = set(mapper._compute_percentiles({1: _stats(1)}))
    assert set(ISSStatMapper.CATEGORY_INPUTS) == computed


def test_the_optional_input_table_names_only_computed_categories(mapper):
    computed = set(mapper._compute_percentiles({1: _stats(1)}))
    assert set(ISSStatMapper.CATEGORY_OPTIONAL_INPUTS) <= computed


def test_the_four_categories_are_the_four_iss_attributes(mapper):
    assert set(ISSStatMapper.CATEGORY_INPUTS) == {"speed", "shooting", "stamina", "technique"}


def test_the_optional_inputs_are_exactly_the_two_espn_dribbling_fields():
    assert set(ISSStatMapper.CATEGORY_OPTIONAL_INPUTS["technique"]) <= set(SOCCER_UNSUPPLIED_STATS)
    assert ISSStatMapper.CATEGORY_OPTIONAL_INPUTS["technique"] == (
        "dribbles_success",
        "dribbles_attempts",
    )


def test_no_required_input_is_a_field_espn_leaves_unsupplied():
    """If one were, ESPN would empty that category for every player and the
    whole league would take the 50th-percentile default."""
    required = {name for names in ISSStatMapper.CATEGORY_INPUTS.values() for name in names}
    assert required.isdisjoint(SOCCER_UNSUPPLIED_STATS)


def test_percentiles_of_an_empty_league_are_empty(mapper):
    assert mapper._compute_percentiles({}) == {}


def test_the_lowest_value_in_a_category_sits_at_zero(mapper):
    all_stats = {i: _stats(i, minutes=100 * i, appearances=1) for i in range(1, 11)}
    assert mapper._compute_percentiles(all_stats)["stamina"][1] == 0.0


def test_the_highest_value_in_a_category_sits_below_a_hundred(mapper):
    """`below / n`, and the player is in `n`, so the top is `(n-1)/n * 100`."""
    all_stats = {i: _stats(i, minutes=100 * i, appearances=1) for i in range(1, 11)}
    assert mapper._compute_percentiles(all_stats)["stamina"][10] == 90.0


def test_tied_values_share_a_percentile(mapper):
    all_stats = {i: _stats(i, minutes=900, appearances=10) for i in range(1, 5)}
    percentiles = mapper._compute_percentiles(all_stats)["stamina"]
    assert set(percentiles.values()) == {0.0}


def test_speed_and_stamina_are_computed_from_the_same_expression(mapper):
    """PRESERVED, reported at the module docstring. Both are
    `minutes / max(appearances, 1)`."""
    all_stats = {i: _stats(i, minutes=100 * i, appearances=i) for i in range(1, 11)}
    percentiles = mapper._compute_percentiles(all_stats)
    assert percentiles["speed"] == percentiles["stamina"]


def test_the_two_categories_are_gated_on_the_same_two_fields(mapper):
    """The other half of why they cannot differ: same formula, same skip.

    A player dropped from one ranking is dropped from the other, so the two
    dicts have the same keys as well as the same values.
    """
    assert ISSStatMapper.CATEGORY_INPUTS["speed"] == ("minutes", "appearances")
    assert ISSStatMapper.CATEGORY_INPUTS["stamina"] == ("minutes", "appearances")


def test_a_players_speed_and_stamina_ratings_are_always_equal(mapper):
    all_stats = {i: _stats(i, minutes=137 * i, appearances=1 + i % 4) for i in range(1, 21)}
    percentiles = mapper._compute_percentiles(all_stats)
    attrs = [mapper.map_player(_player(i), all_stats[i], percentiles) for i in range(1, 21)]
    assert [a.speed for a in attrs] == [a.stamina for a in attrs]


def test_the_ratings_are_not_all_the_same_value(mapper):
    """Guards the test above from being satisfied by a constant.

    Stated as the exact set the twenty players produce, not as "more than one":
    a mapper that collapsed the scale to two values would satisfy a bound and
    fails this.
    """
    all_stats = {i: _stats(i, minutes=137 * i, appearances=1) for i in range(1, 21)}
    percentiles = mapper._compute_percentiles(all_stats)
    attrs = [mapper.map_player(_player(i), all_stats[i], percentiles) for i in range(1, 21)]
    assert sorted({a.speed for a in attrs}) == [1, 2, 4, 6, 8, 10, 12, 14, 16]


def test_the_unmeasured_player_is_the_one_whose_speed_and_stamina_differ(mapper):
    """The claim the module docstring used to overstate as "every player".

    The collapse is a property of the ranking, not of the record: a player the
    provider has no appearances for takes `_fallback_attributes` instead, whose
    four rows all give speed and stamina different numbers. Both paths appear in
    one call here, so this cannot pass by everyone taking the same one.
    """
    all_stats = {i: _stats(i, minutes=90 * i, appearances=i) for i in range(1, 6)}
    all_stats[6] = _stats(6, appearances=0, minutes=0)
    percentiles = mapper._compute_percentiles(all_stats)
    measured = [
        mapper.map_player(_player(i, "Defender"), all_stats[i], percentiles) for i in range(1, 6)
    ]
    assert [a.speed for a in measured] == [a.stamina for a in measured]
    unmeasured = mapper.map_player(_player(6, "Defender"), all_stats[6], percentiles)
    assert unmeasured.speed == 8
    assert unmeasured.stamina == 9


@pytest.mark.parametrize(
    ("position", "speed", "stamina"),
    [("Goalkeeper", 6, 8), ("Defender", 8, 9), ("Midfielder", 8, 10), ("Attacker", 10, 7)],
)
def test_no_fallback_row_gives_a_player_the_same_speed_and_stamina(
    mapper, position, speed, stamina
):
    """All four rows, at their values, so a row that became equal fails here."""
    attrs = mapper._fallback_attributes(_player(1, position, age=27))
    assert attrs.speed == speed
    assert attrs.stamina == stamina


def test_zero_appearances_does_not_divide_by_zero(mapper):
    all_stats = {1: _stats(1, appearances=0, minutes=0), 2: _stats(2)}
    assert mapper._compute_percentiles(all_stats)["stamina"][1] == 0.0


def test_technique_uses_both_halves_when_dribbling_is_measured(mapper):
    stats = _stats(1, dribbles_success=30, dribbles_attempts=60, passes_accuracy=80.0)
    assert mapper._technique_value(stats) == (50.0 + 80.0) / 2


def test_technique_falls_back_to_pass_accuracy_alone_when_it_is_not(mapper):
    stats = _stats(1, dribbles_success=0, dribbles_attempts=0, passes_accuracy=80.0)
    stats.unsupplied = SOCCER_UNSUPPLIED_STATS
    assert mapper._technique_value(stats) == 80.0


def test_the_fallback_ignores_dribbling_values_the_record_declares_filler(mapper):
    """DELIBERATE DIVERGENCE, and the only regime where output differs from
    upstream's. A record that names a field unsupplied while still carrying a
    measurement for it is not something any provider here builds --
    `EspnClient._parse_athlete_stats` writes 0 and declares it -- so this is a
    statement about which of the two the code believes."""
    stats = _stats(1, dribbles_success=60, dribbles_attempts=60, passes_accuracy=40.0)
    stats.unsupplied = SOCCER_UNSUPPLIED_STATS
    assert mapper._technique_value(stats) == 40.0


def test_the_espn_fallback_preserves_the_ranking_pass_accuracy_gives(mapper):
    """Why the ROM is byte-identical: a percentile is a rank, and `pa` is a
    strictly increasing transform of upstream's `(0 + pa) / 2`."""
    accuracies = [40.0, 55.5, 61.0, 72.25, 88.0]
    espn = {}
    naive = {}
    for i, accuracy in enumerate(accuracies, start=1):
        espn[i] = _stats(
            i,
            passes_accuracy=accuracy,
            dribbles_success=0,
            dribbles_attempts=0,
            unsupplied=SOCCER_UNSUPPLIED_STATS,
        )
        naive[i] = _stats(i, passes_accuracy=accuracy, dribbles_success=0, dribbles_attempts=0)
    gated = mapper._compute_percentiles(espn)["technique"]
    ungated = mapper._compute_percentiles(naive)["technique"]
    assert gated == ungated


def test_the_espn_fallback_still_separates_players(mapper):
    """The declared quality regression is a loss of *signal*, not a collapse:
    every player keeps a distinct rank on pass accuracy."""
    all_stats = {
        i: _stats(
            i,
            passes_accuracy=40.0 + i * 2,
            dribbles_success=0,
            dribbles_attempts=0,
            unsupplied=SOCCER_UNSUPPLIED_STATS,
        )
        for i in range(1, 11)
    }
    percentiles = mapper._compute_percentiles(all_stats)["technique"]
    assert len(set(percentiles.values())) == 10


def test_dropping_the_player_instead_would_have_flattened_the_category(mapper):
    """The design note, made checkable. `passes_accuracy` is a *required* input
    and dribbling is optional; had dribbling been required, every ESPN record
    would be skipped and the category would come back empty."""
    all_stats = {
        i: _stats(i, unsupplied=SOCCER_UNSUPPLIED_STATS + ("passes_accuracy",))
        for i in range(1, 11)
    }
    assert mapper._compute_percentiles(all_stats)["technique"] == {}


def test_an_empty_category_gives_every_player_the_midpoint_rating(mapper):
    """What the paragraph above is avoiding: `.get(pid, 50)` and a 50th
    percentile is 9 on the odd 1-15 scale."""
    attrs = mapper.map_player(_player(1), _stats(1), {"technique": {}})
    assert attrs.technique == 9


def test_a_player_missing_a_required_input_is_left_out_of_that_category(mapper):
    all_stats = {
        1: _stats(1, unsupplied=("minutes",)),
        2: _stats(2, minutes=1000),
        3: _stats(3, minutes=2000),
    }
    percentiles = mapper._compute_percentiles(all_stats)
    assert 1 not in percentiles["stamina"]
    assert 1 in percentiles["shooting"]


def test_the_skipped_player_does_not_drag_the_denominator(mapper):
    """`n` is per category. With three players and one skipped the top of the
    remaining two is 50, not 33."""
    all_stats = {
        1: _stats(1, unsupplied=("minutes",)),
        2: _stats(2, minutes=1000),
        3: _stats(3, minutes=2000),
    }
    assert mapper._compute_percentiles(all_stats)["stamina"][3] == 50.0


@pytest.mark.parametrize(
    ("percentile", "expected"),
    [(100, 15), (95, 15), (94, 13), (85, 13), (70, 11), (50, 9), (35, 7), (20, 5), (10, 3), (0, 1)],
)
def test_the_shooting_table_maps_percentiles_to_odd_values(mapper, percentile, expected):
    assert mapper._percentile_to_shooting(percentile) == expected


def test_a_negative_percentile_falls_off_the_bottom_of_the_shooting_table(mapper):
    assert mapper._percentile_to_shooting(-1) == 1


@pytest.mark.parametrize(
    ("percentile", "expected"),
    [(100, 16), (95, 16), (88, 14), (75, 12), (60, 10), (45, 8), (30, 6), (15, 4), (5, 2), (0, 1)],
)
def test_the_speed_table_maps_percentiles_to_the_one_to_sixteen_scale(mapper, percentile, expected):
    assert mapper._percentile_to_speed(percentile) == expected


def test_a_negative_percentile_falls_off_the_bottom_of_the_speed_table(mapper):
    assert mapper._percentile_to_speed(-1) == 1


def test_every_shooting_value_the_table_produces_is_odd(mapper):
    values = {rating for _, rating in ISSStatMapper.SHOOTING_TABLE}
    assert sorted(values) == [1, 3, 5, 7, 9, 11, 13, 15]


def test_a_player_with_no_stats_gets_his_positions_defaults(mapper):
    attrs = mapper.map_player(_player(1, "Goalkeeper"), None, {})
    assert attrs.speed == 6
    assert attrs.shooting == 3


def test_a_player_with_zero_appearances_gets_his_positions_defaults(mapper):
    attrs = mapper.map_player(_player(1, "Attacker"), _stats(1, appearances=0), {})
    assert attrs.shooting == 11


def test_an_unknown_position_falls_back_to_the_midfielder_defaults(mapper):
    assert mapper._fallback_attributes(_player(1, "Sweeper")) == mapper._fallback_attributes(
        _player(1, "Midfielder")
    )


def test_a_young_player_gets_a_speed_and_stamina_bonus(mapper):
    young = mapper._fallback_attributes(_player(1, "Defender", age=21))
    plain = mapper._fallback_attributes(_player(1, "Defender", age=27))
    assert young.speed == plain.speed + 2
    assert young.stamina == plain.stamina + 1


def test_an_old_player_loses_pace_and_gains_technique(mapper):
    old = mapper._fallback_attributes(_player(1, "Defender", age=35))
    plain = mapper._fallback_attributes(_player(1, "Defender", age=27))
    assert old.speed == plain.speed - 2
    assert old.technique == plain.technique + 2


def test_the_age_bonus_cannot_push_a_rating_past_the_scale(mapper):
    """Attacker speed is 10 and the bonus is 2, so this needs a table change to
    be reachable; the clamp is asserted at the value it clamps to."""
    attrs = mapper._fallback_attributes(_player(1, "Attacker", age=20))
    assert attrs.speed == 12


def test_a_player_with_no_age_gets_neither_adjustment(mapper):
    assert mapper._fallback_attributes(_player(1, "Defender", age=0)) == (
        mapper._fallback_attributes(_player(1, "Defender", age=27))
    )


def test_each_call_builds_its_own_attributes_object(mapper):
    """`FALLBACK_ATTRS` holds dicts, and `ISSPlayerAttributes(**defaults)`
    constructs from them, so the age adjustment cannot leak between players."""
    first = mapper._fallback_attributes(_player(1, "Defender", age=21))
    second = mapper._fallback_attributes(_player(2, "Defender", age=27))
    assert first.speed != second.speed


def test_a_prolific_scorer_is_a_star(mapper):
    assert mapper._is_star_player(_player(1), _stats(1, appearances=20, goals=12)) is True


def test_a_prolific_creator_is_a_star(mapper):
    assert mapper._is_star_player(_player(1), _stats(1, appearances=20, goals=0, assists=9)) is True


def test_a_player_just_under_both_thresholds_is_not(mapper):
    assert (
        mapper._is_star_player(_player(1), _stats(1, appearances=20, goals=9, assists=7)) is False
    )


def test_fewer_than_five_appearances_disqualifies_however_good_the_rate(mapper):
    assert mapper._is_star_player(_player(1), _stats(1, appearances=4, goals=4)) is False


def test_a_player_with_no_stats_is_not_a_star(mapper):
    assert mapper._is_star_player(_player(1), None) is False


def _squad(counts):
    players = []
    index = 1
    for position, count in counts.items():
        for _ in range(count):
            players.append(_player(index, position))
            index += 1
    return players


def test_a_full_squad_is_cut_to_fifteen(mapper):
    players = _squad({"Goalkeeper": 3, "Defender": 8, "Midfielder": 8, "Attacker": 5})
    assert len(mapper._select_best_15(players, {})) == PLAYERS_PER_TEAM


def test_the_starting_eleven_is_one_keeper_four_defenders_four_midfielders_two_forwards(mapper):
    players = _squad({"Goalkeeper": 3, "Defender": 8, "Midfielder": 8, "Attacker": 5})
    eleven = mapper._select_best_15(players, {})[:11]
    positions = [p.position for p in eleven]
    assert positions == ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Attacker"] * 2


def test_the_first_substitute_is_the_backup_keeper(mapper):
    players = _squad({"Goalkeeper": 3, "Defender": 8, "Midfielder": 8, "Attacker": 5})
    assert mapper._select_best_15(players, {})[11].position == "Goalkeeper"


def test_the_squad_is_ordered_by_starts_then_appearances_then_minutes(mapper):
    players = [_player(i, "Defender") for i in range(1, 6)]
    stats = {i: _stats(i, lineups=i, appearances=10, minutes=900) for i in range(1, 6)}
    chosen = mapper._select_best_15(players, stats)[:4]
    assert [p.id for p in chosen] == [5, 4, 3, 2]


def test_a_player_with_no_stats_sorts_behind_one_with_any(mapper):
    players = [_player(1, "Defender"), _player(2, "Defender")]
    stats = {2: _stats(2, lineups=1)}
    assert [p.id for p in mapper._select_best_15(players, stats)] == [2, 1]


def test_a_squad_shorter_than_fifteen_is_returned_whole(mapper):
    players = _squad({"Goalkeeper": 1, "Defender": 3, "Midfielder": 2})
    assert len(mapper._select_best_15(players, {})) == 6


def test_a_player_in_an_unknown_position_is_still_picked_up(mapper):
    players = [_player(1, "Sweeper"), _player(2, "Goalkeeper")]
    assert {p.id for p in mapper._select_best_15(players, {})} == {1, 2}


def test_no_player_appears_twice_in_the_selection(mapper):
    players = _squad({"Goalkeeper": 3, "Defender": 8, "Midfielder": 8, "Attacker": 5})
    chosen = mapper._select_best_15(players, {})
    assert len({p.id for p in chosen}) == len(chosen)


def test_an_empty_squad_selects_nobody(mapper):
    assert mapper._select_best_15([], {}) == []


def test_a_two_word_name_reduces_to_the_surname(mapper):
    assert mapper._format_player_name(_player(1, name="Jurgen Klinsmann")) == "Klinsman"


def test_a_single_word_name_is_used_whole(mapper):
    assert mapper._format_player_name(_player(1, name="Ronaldinho")) == "Ronaldin"


def test_a_surname_longer_than_eight_characters_is_cut(mapper):
    assert mapper._format_player_name(_player(1, name="Marc Vanderbeek")) == "Vanderbe"


def test_a_player_with_no_name_falls_back_to_his_last_name(mapper):
    player = Player(id=1, name="", last_name="Nakata", position="Midfielder")
    assert mapper._format_player_name(player) == "Nakata"


def test_a_player_with_no_name_at_all_becomes_PLAYER(mapper):
    player = Player(id=1, name="", last_name="", position="Midfielder")
    assert mapper._format_player_name(player) == "PLAYER"


def test_diacritics_are_stripped_before_truncation(mapper):
    assert mapper._format_player_name(_player(1, name="José Giménez")) == "Gimenez"


def test_mapping_a_team_produces_fifteen_records(mapper):
    players = _squad({"Goalkeeper": 3, "Defender": 8, "Midfielder": 8, "Attacker": 5})
    roster = _roster(players, [_stats(p.id) for p in players])
    record = mapper.map_team_with_league_context(roster, [roster])
    assert len(record.players) == PLAYERS_PER_TEAM


def test_the_team_record_carries_the_ascii_folded_name(mapper):
    roster = _roster([], [])
    roster.team = Team(id=1, name="Atlético Madrid", code="ATM")
    record = mapper.map_team_with_league_context(roster, [roster])
    assert record.name == "Atletico Madrid"


def test_the_short_name_comes_from_the_team_code(mapper):
    roster = _roster([], [])
    assert mapper.map_team_with_league_context(roster, [roster]).short_name == "TST"


def test_a_team_with_no_code_takes_three_upper_case_letters_of_its_name(mapper):
    roster = _roster([], [])
    roster.team = Team(id=1, name="Feyenoord", code="")
    assert mapper.map_team_with_league_context(roster, [roster]).short_name == "FEY"


def test_the_shirt_number_defaults_to_one_when_the_provider_has_none(mapper):
    player = Player(id=1, name="A B", position="Midfielder", number=None)
    roster = _roster([player], [])
    record = mapper.map_team_with_league_context(roster, [roster])
    assert record.players[0].shirt_number == 1


def test_the_hair_style_is_chosen_by_position(mapper):
    players = [_player(1, "Goalkeeper"), _player(2, "Attacker")]
    roster = _roster(players, [])
    record = mapper.map_team_with_league_context(roster, [roster])
    assert record.players[0].hair_style == 0
    assert record.players[1].hair_style == 4


def test_ratings_are_normalised_across_the_whole_league_and_not_one_team(mapper):
    """Two teams, one strong and one weak. Passing only the team would rate its
    own worst player at the bottom of a one-team league."""
    strong = [_player(i, "Midfielder") for i in range(1, 6)]
    weak = [_player(i, "Midfielder") for i in range(6, 11)]
    strong_roster = _roster(strong, [_stats(p.id, minutes=1800, appearances=20) for p in strong])
    # Spread, so the weak team alone spans the whole 0-80 percentile range and
    # a collapse to a single rating cannot be mistaken for the effect measured.
    weak_roster = _roster(weak, [_stats(p.id, minutes=100 * p.id, appearances=20) for p in weak])
    weak_roster.team = Team(id=2, name="Weak", code="WEA")
    alone = mapper.map_team_with_league_context(weak_roster, [weak_roster])
    in_league = mapper.map_team_with_league_context(weak_roster, [strong_roster, weak_roster])
    assert [p.attributes.stamina for p in alone.players] == [12, 10, 6, 4, 1]
    assert [p.attributes.stamina for p in in_league.players] == [6, 6, 4, 2, 1]


def test_the_record_carries_no_flag_colours_of_its_own(mapper):
    """The mapper knows nothing about colours: `ISSPatcher._apply_colours` fills
    them in from the provider's `Team`."""
    roster = _roster([], [])
    assert mapper.map_team_with_league_context(roster, [roster]).flag_colors == []
