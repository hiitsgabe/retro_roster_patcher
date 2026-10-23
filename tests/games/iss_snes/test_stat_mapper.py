"""The ISS stat mapper: nine ability fields, calibrated to the retail records.

Every field must land inside the range the 390 retail non-Super-Star records
use, because the kick-off condition takes up to 5 off every field and a value
below retail plays worse than it reads. Speed has no ESPN source and comes from
position and age; stamina has its own input and is no longer speed's twin.

`technique` is half dribbling and half passing, and ESPN measures no dribbling at
all. The gate is `CATEGORY_OPTIONAL_INPUTS`, and it is NOT WE2002's rule:
dropping the player from the category would leave it empty and give every player
the same rating, so the dribbling *term* is dropped instead, which preserves the
ranking exactly.
"""

from __future__ import annotations

import dataclasses
import statistics

import pytest

from retro_roster_patcher.games.iss_snes.models import PLAYERS_PER_TEAM, ISSPlayerAttributes
from retro_roster_patcher.games.iss_snes.stat_mapper import ISSStatMapper, _lookup
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


def test_tied_values_share_the_mean_of_their_ranks(mapper):
    """Four ties span ranks 0-3, so all four sit at 1.5 / 4. At the bottom
    instead, a position where nobody scores (keepers) would all lose offense."""
    all_stats = {i: _stats(i, minutes=900, appearances=10) for i in range(1, 5)}
    percentiles = mapper._compute_percentiles(all_stats)["stamina"]
    assert set(percentiles.values()) == {37.5}


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


def test_an_empty_category_gives_every_player_the_retail_median(mapper):
    """What the paragraph above is avoiding: `.get(pid, 50)`, and the 50th
    percentile is technique 3, the retail median."""
    attrs = mapper.map_player(_player(1), _stats(1), {"technique": {}})
    assert attrs.technique == 3


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


def _squad(counts):
    players = []
    index = 1
    for position, count in counts.items():
        for _ in range(count):
            players.append(_player(index, position))
            index += 1
    return players


def test_the_categories_are_the_six_the_stats_drive():
    """Speed is not one of them: nothing ESPN reports measures pace."""
    assert set(ISSStatMapper.CATEGORY_INPUTS) == {
        "shooting",
        "technique",
        "stamina",
        "kick",
        "offense",
        "defense",
    }


#: Retail p10 / median / p90 over the 390 non-Super-Star records, the floor the
#: mapper never goes below, and the top value the field's bits hold.
RETAIL = {
    "SHOOTING_TABLE": ((2, 4, 5), 1, 7),
    "TECHNIQUE_TABLE": ((1, 3, 5), 1, 7),
    "STAMINA_TABLE": ((8, 10, 13), 7, 15),
    "KICK_TABLE": ((2, 4, 6), 2, 7),
}


@pytest.mark.parametrize(("table", "retail"), RETAIL.items())
def test_each_table_puts_the_leagues_p10_median_and_p90_on_retails(table, retail):
    quantiles, floor, top = retail
    rows = getattr(ISSStatMapper, table)
    assert tuple(_lookup(rows, p) for p in (10, 50, 90)) == quantiles
    assert _lookup(rows, 0) == _lookup(rows, -1) == floor


@pytest.mark.parametrize(("table", "retail"), RETAIL.items())
def test_the_top_value_is_kept_for_the_top_few_percent(table, retail):
    rows = getattr(ISSStatMapper, table)
    top = retail[2]
    assert _lookup(rows, 95) < top == _lookup(rows, 99.9)


@pytest.mark.parametrize(
    ("position", "defense", "offense", "dribble", "dash", "ball_dash"),
    [
        # Keepers from retail slot 0 alone: slow, and all defense.
        ("Goalkeeper", 12, 2, 5, 6, 5),
        ("Defender", 11, 4, 7, 10, 9),
        ("Midfielder", 8, 7, 7, 10, 9),
        ("Attacker", 4, 10, 8, 11, 10),
        ("Sweeper", 8, 7, 7, 10, 9),
    ],
)
def test_a_player_without_stats_gets_his_positions_baselines_and_the_retail_medians(
    mapper, position, defense, offense, dribble, dash, ball_dash
):
    for stats in (None, _stats(1, appearances=0)):
        attrs = mapper.map_player(_player(1, position, age=27), stats, {})
        assert attrs == ISSPlayerAttributes(
            dribble=dribble,
            dash=dash,
            ball_dash=ball_dash,
            defense=defense,
            offense=offense,
        )


def test_no_speed_falls_below_retails_slowest(mapper):
    old_keeper = mapper.map_player(_player(1, "Goalkeeper", age=38), None, {})
    assert (old_keeper.dribble, old_keeper.dash, old_keeper.ball_dash) == (4, 4, 4)


@pytest.mark.parametrize(
    ("age", "shift"),
    [(0, 0), (19, 1), (23, 1), (24, 0), (30, 0), (31, -1), (33, -1), (34, -2), (38, -2)],
)
def test_age_moves_all_three_speeds_together(mapper, age, shift):
    """Unknown age (0) moves nothing. The order dribble <= ball_dash <= dash
    holds at every age."""
    for stats in (None, _stats(1)):
        attrs = mapper.map_player(_player(1, "Defender", age=age), stats, {})
        assert (attrs.dribble, attrs.ball_dash, attrs.dash) == (7 + shift, 9 + shift, 10 + shift)


def test_stamina_comes_from_minutes_per_appearance_and_not_from_speed(mapper):
    all_stats = {i: _stats(i, minutes=500 + 80 * i, appearances=10) for i in range(1, 21)}
    percentiles = mapper._compute_percentiles(all_stats)
    attrs = [mapper.map_player(_player(i, age=27), all_stats[i], percentiles) for i in range(1, 21)]
    staminas = [a.stamina for a in attrs]
    assert staminas == sorted(staminas)
    assert (staminas[0], staminas[-1]) == (7, 14)  # 15 needs the top 3 percent
    assert {a.dash for a in attrs} == {10}


def test_shooting_ranks_goals_then_shots_on_target(mapper):
    all_stats = {
        1: _stats(1, goals=0, shots_on=0),
        2: _stats(2, goals=0, shots_on=10),
        3: _stats(3, goals=10, shots_on=10),
    }
    ranks = mapper._compute_percentiles(all_stats)["shooting"]
    assert ranks[1] < ranks[2] < ranks[3]


def test_kick_comes_from_passes_per_game(mapper):
    all_stats = {i: _stats(i, passes_total=100 * i, appearances=10) for i in range(1, 41)}
    percentiles = mapper._compute_percentiles(all_stats)
    kicks = [mapper.map_player(_player(i), all_stats[i], percentiles).kick for i in range(1, 41)]
    assert kicks == sorted(kicks)
    assert (kicks[0], kicks[-1]) == (2, 7)


def test_without_passes_kick_is_the_mean_of_shooting_and_technique(mapper):
    stats = _stats(1, unsupplied=("passes_total",))
    assert 1 not in mapper._compute_percentiles({1: stats, 2: _stats(2)})["kick"]
    percentiles = {"shooting": {1: 99.0}, "technique": {1: 30.0}}  # 7 and 3
    assert mapper.map_player(_player(1), stats, percentiles).kick == 5
    odd = {"shooting": {1: 50.0}, "technique": {1: 30.0}}  # 4 and 3 round half up
    assert mapper.map_player(_player(1), stats, odd).kick == 4


def test_the_kick_fallback_never_goes_below_retails_floor(mapper):
    percentiles = {"shooting": {1: 0.0}, "technique": {1: 0.0}}  # 1 and 1
    stats = _stats(1, unsupplied=("passes_total",))
    assert mapper.map_player(_player(1), stats, percentiles).kick == 2


def test_offense_ranks_a_player_against_his_own_position(mapper):
    """The best-scoring defender gets +2 although every forward scores more,
    and the worst forward -2 although he outscores every defender."""
    all_stats = {i: _stats(i, goals=i, assists=0, appearances=30) for i in range(1, 21)}
    positions = {i: "Defender" if i <= 10 else "Attacker" for i in all_stats}
    percentiles = mapper._compute_percentiles(all_stats, positions)
    best_defender = mapper.map_player(_player(10, "Defender"), all_stats[10], percentiles)
    worst_forward = mapper.map_player(_player(11, "Attacker"), all_stats[11], percentiles)
    assert best_defender.offense == 4 + 2
    assert worst_forward.offense == 10 - 2


def test_a_position_that_never_scores_keeps_its_offense_baseline(mapper):
    """Ties share the middle rank, not the bottom."""
    all_stats = {i: _stats(i, goals=0, assists=0) for i in range(1, 11)}
    percentiles = mapper._compute_percentiles(all_stats, dict.fromkeys(all_stats, "Defender"))
    defender = mapper.map_player(_player(1, "Defender"), all_stats[1], percentiles)
    assert defender.offense == 4


def test_keepers_keep_their_offense_and_defense_baselines(mapper):
    """A keeper's assists, tackles and interceptions move neither field."""
    all_stats = {
        i: _stats(i, goals=0, assists=i, tackles_total=i, interceptions=i) for i in range(1, 11)
    }
    percentiles = mapper._compute_percentiles(all_stats, dict.fromkeys(all_stats, "Goalkeeper"))
    keepers = [
        mapper.map_player(_player(i, "Goalkeeper"), all_stats[i], percentiles) for i in all_stats
    ]
    assert {(k.defense, k.offense) for k in keepers} == {(12, 2)}


def test_technique_and_kick_rank_within_each_position(mapper):
    """Centre-backs pass more, and more accurately, than forwards: ranked across
    the league they would take every top technique and kick. Ranked within the
    position, each position gets the same spread."""
    players, all_stats = [], {}
    for i in range(1, 41):
        position = "Defender" if i <= 20 else "Attacker"
        rank = i if i <= 20 else i - 20
        players.append(_player(i, position))
        all_stats[i] = _stats(
            i,
            passes_accuracy=(85 if position == "Defender" else 65) + rank * 0.4,
            passes_total=(60 if position == "Defender" else 25) * 20 + rank,
            appearances=20,
        )
    percentiles = mapper._compute_percentiles(all_stats, {p.id: p.position for p in players})
    by_position = {}
    for p in players:
        attrs = mapper.map_player(p, all_stats[p.id], percentiles)
        by_position.setdefault(p.position, []).append((attrs.technique, attrs.kick))
    assert sorted(by_position["Defender"]) == sorted(by_position["Attacker"])


def test_defense_moves_with_tackles_and_interceptions_per_game(mapper):
    all_stats = {i: _stats(i, tackles_total=i, interceptions=i) for i in range(1, 11)}
    percentiles = mapper._compute_percentiles(all_stats, dict.fromkeys(all_stats, "Midfielder"))
    defense = [
        mapper.map_player(_player(i, "Midfielder"), all_stats[i], percentiles).defense
        for i in range(1, 11)
    ]
    assert defense == [6, 7, 7, 8, 8, 8, 8, 9, 9, 10]


def test_defense_stays_at_the_baseline_without_tackles(mapper):
    all_stats = {i: _stats(i, tackles_total=i, unsupplied=("tackles_total",)) for i in range(1, 11)}
    percentiles = mapper._compute_percentiles(all_stats, dict.fromkeys(all_stats, "Defender"))
    assert percentiles["defense"] == {}
    defense = {
        mapper.map_player(_player(i, "Defender"), all_stats[i], percentiles).defense
        for i in all_stats
    }
    assert defense == {11}


#: What a mapped league must stay inside: retail's range for each field, cut to
#: what the calibration can reach. Speed nibbles never reach 15.
RETAIL_BANDS = dict(
    dribble=(4, 9),
    dash=(4, 12),
    ball_dash=(4, 11),
    kick=(2, 7),
    shooting=(1, 7),
    technique=(1, 7),
    stamina=(7, 15),
    defense=(2, 13),
    offense=(1, 12),
)


def test_a_mapped_league_stays_inside_the_retail_ranges(mapper):
    """400 players with spread stats, every position and age 17-38."""
    positions = ("Goalkeeper", "Defender", "Midfielder", "Attacker")
    players = [_player(i, positions[i % 4], age=17 + i % 22) for i in range(400)]
    all_stats = {
        i: _stats(
            i,
            appearances=1 + i % 38,
            minutes=(i * 37) % 3400,
            goals=(i * 7) % 23,
            assists=(i * 5) % 13,
            shots_on=(i * 11) % 41,
            passes_total=(i * 53) % 2500,
            passes_accuracy=50 + (i * 13) % 45,
            tackles_total=(i * 3) % 90,
            interceptions=(i * 17) % 60,
            unsupplied=SOCCER_UNSUPPLIED_STATS,
        )
        for i in range(400)
    }
    percentiles = mapper._compute_percentiles(all_stats, {p.id: p.position for p in players})
    league = [
        dataclasses.asdict(mapper.map_player(p, all_stats[p.id], percentiles)) for p in players
    ]
    for attrs in league:
        for field, (low, high) in RETAIL_BANDS.items():
            assert low <= attrs[field] <= high, (field, attrs)
        assert attrs["dribble"] <= attrs["ball_dash"] <= attrs["dash"]
    for field, median in (("shooting", 4), ("technique", 3), ("stamina", 10), ("kick", 4)):
        assert statistics.median(a[field] for a in league) == median, field
    for field, top in (("shooting", 7), ("technique", 7), ("stamina", 15), ("kick", 7)):
        assert sum(a[field] == top for a in league) <= 0.04 * len(league), field


def _numbered(*numbers):
    return [_player(i, number=n) for i, n in enumerate(numbers, start=1)]


def test_a_free_real_number_stays_and_the_rest_take_the_lowest_free_in_squad_order(mapper):
    """The second 10 is taken, 23 and None are out of range: 2, 3, 4 in turn."""
    assert mapper._shirt_numbers(_numbered(10, 10, 23, None, 1)) == [10, 2, 3, 4, 1]


def test_a_mapped_team_wears_fifteen_different_numbers_from_one_to_fifteen(mapper):
    players = _squad({"Goalkeeper": 3, "Defender": 8, "Midfielder": 8, "Attacker": 5})
    for player in players:
        player.number = 7 if player.id % 2 else 20 + player.id
    roster = _roster(players, [_stats(p.id) for p in players])
    record = mapper.map_team_with_league_context(roster, [roster])
    assert sorted(p.shirt_number for p in record.players) == list(range(1, 16))


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
    assert [p.attributes.stamina for p in alone.players] == [12, 11, 10, 9, 7]
    assert [p.attributes.stamina for p in in_league.players] == [10, 9, 9, 8, 7]


def test_the_record_carries_no_flag_colours_of_its_own(mapper):
    """The mapper knows nothing about colours: `ISSPatcher._apply_colours` fills
    them in from the provider's `Team`."""
    roster = _roster([], [])
    assert mapper.map_team_with_league_context(roster, [roster]).flag_colors == []


def _lineup(*starters):
    """A `get_lineup` result: (player id, ESPN position code) in start order."""
    return {
        "formation": "",
        "event": 1,
        "starters": [{"id": i, "position": c} for i, c in starters],
    }


def test_the_recent_lineup_starts_over_the_season_leaders(mapper):
    players = _squad({"Goalkeeper": 2, "Defender": 6, "Midfielder": 6, "Attacker": 3})
    # Season starts favour the low ids; the recent eleven is the high ones.
    stats = {p.id: _stats(p.id, lineups=30 - p.id) for p in players}
    lineup = _lineup(
        (2, "G"),
        (7, "RB"),
        (8, "CD-R"),
        (6, "CD-L"),
        (5, "LB"),
        (13, "DM"),
        (14, "CM"),
        (12, "AM"),
        (11, "LM"),
        (17, "CF"),
        (16, "RF"),
    )
    eleven = mapper._select_best_15(players, stats, lineup)[:11]
    assert {p.id for p in eleven} == {2, 7, 8, 6, 5, 13, 14, 12, 11, 17, 16}
    assert eleven[0].id == 2  # the recent keeper keeps goal


def test_a_front_three_puts_its_third_forward_in_midfield(mapper):
    players = _squad({"Goalkeeper": 2, "Defender": 6, "Midfielder": 6, "Attacker": 4})
    lineup = _lineup(
        (1, "G"),
        (3, "LB"),
        (4, "CD"),
        (5, "CD"),
        (6, "RB"),
        (9, "DM"),
        (10, "CM"),
        (11, "CM"),
        (15, "LF"),
        (16, "CF"),
        (17, "RF"),
    )
    eleven = mapper._select_best_15(players, {}, lineup)[:11]
    assert {p.id for p in eleven} == {1, 3, 4, 5, 6, 9, 10, 11, 15, 16, 17}
    assert [p.position for p in eleven][5:9].count("Attacker") == 1  # one forward drops back


def test_a_forward_with_no_starts_never_takes_a_place_from_a_starter(mapper):
    # One forward has starts; the other never started. A midfielder with
    # starts takes the second forward place instead.
    players = _squad({"Goalkeeper": 1, "Defender": 4, "Midfielder": 5, "Attacker": 2})
    stats = {p.id: _stats(p.id, lineups=10) for p in players if p.id != 12}
    eleven = mapper._select_best_15(players, stats)[:11]
    assert 12 not in {p.id for p in eleven}
    assert {p.id for p in eleven} == {p.id for p in players if p.id != 12}


def test_shared_surnames_are_told_apart(mapper):
    names = mapper._distinct_names(
        [
            _player(1, name="Vitor Hugo"),
            _player(2, name="Victor Hugo"),
            _player(3, name="Gabriel Silva"),
            _player(4, name="Lucas Silva"),
            _player(5, name="Paulinho"),
            _player(6, name="Paulinho"),
            _player(7, name="Bernard"),
        ]
    )
    assert names == ["Vitor", "Victor", "G.Silva", "L.Silva", "Paulinho", "Paulinh2", "Bernard"]
    assert len(set(names)) == len(names)


def test_only_the_written_fifteens_set_the_scale(mapper):
    """Bench and youth players with stats used to push every written player up
    the scale: what they record must not move the fifteen's ratings."""
    team = _squad({"Goalkeeper": 3, "Defender": 9, "Midfielder": 9, "Attacker": 9})
    stats = [_stats(p.id, lineups=40 - p.id, goals=p.id % 5, shots_on=p.id % 7) for p in team]
    roster = _roster(team, stats)
    before = mapper.map_team_with_league_context(roster, [roster])
    chosen = {p.id for p in mapper._select_best_15(team, roster.player_stats)}
    roster.player_stats.update(
        {
            p.id: _stats(p.id, lineups=0, goals=30, shots_on=90, passes_accuracy=99, minutes=3400)
            for p in team
            if p.id not in chosen
        }
    )
    after = mapper.map_team_with_league_context(roster, [roster])
    assert [p.attributes for p in after.players] == [p.attributes for p in before.players]
