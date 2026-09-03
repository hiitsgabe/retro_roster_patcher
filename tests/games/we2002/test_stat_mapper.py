"""The two places `StatMapper` turns provider data into something the ROM stores.

`_format_player_name` is the one place a provider string becomes ROM bytes
without passing through a numeric mapping, so it is where a malformed payload
gets to decide whether the whole patch runs. It has three fallbacks — display
name to last name, and forename to the display name's first word — and each of
them was reached by testing a string for truthiness, which a string of spaces
passes.

The second half of this file is the rating path, and specifically what happens to
an attribute whose statistic the player's provider does not measure. A filler
zero is not a measurement of zero: percentiled against a league it lands on the
floor, so a provider-wide absence rates every player in the game at the minimum
for that attribute while the attributes that are supplied stay correct and make
a spot check look fine. `PlayerStats.unsupplied` is what tells the two apart, and
every test below that touches it is written so that collapsing the attribute to a
constant fails it.
"""

from dataclasses import fields, replace

import pytest

from retro_roster_patcher.games.we2002.stat_mapper import StatMapper
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


# ── the absent display name ──────────────────────────────────────────────


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


# ── names that become whitespace on the way to ASCII ─────────────────────


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


# ── padding around a real name ───────────────────────────────────────────


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


# ── the shapes that already worked ───────────────────────────────────────


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


# ── through the mapper's own entry point ─────────────────────────────────


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

    # Sorted, because `_select_best_22` orders by position and rating and this
    # test is about none of the three being lost, not about their order.
    assert sorted(p.last_name for p in record.players) == ["Neymar", "Silva", "V. Hugo"]
    assert len(record.players) == 3


# ── the stats a provider does not measure ────────────────────────────────

# The four `PlayerStats` fields ESPN's soccer statistics document has no
# counterpart for. Spelled out here rather than imported from the client, so that
# a change to the client's constant fails these tests instead of quietly
# redefining what they assert.
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

# One of each position and a spread of ages, which is what a real squad is. Ages
# are chosen to straddle every band the three estimators switch on, so an
# estimator that ignored age would collapse each attribute to one value per
# position and fail the distinctness assertions below.
SQUAD = [
    Player(id=1, name="Keeper One", position="Goalkeeper", age=22),
    Player(id=2, name="Back Two", position="Defender", age=28),
    Player(id=3, name="Back Three", position="Defender", age=35),
    Player(id=4, name="Mid Four", position="Midfielder", age=20),
    Player(id=5, name="Mid Five", position="Midfielder", age=31),
    Player(id=6, name="Front Six", position="Attacker", age=26),
    Player(id=7, name="Front Seven", position="Attacker", age=33),
]


def _squad_stats(unsupplied=()):
    """One `PlayerStats` per player in `SQUAD`, all identical but for the id.

    Identical on purpose: every difference the tests below observe in
    `body_balance`, `technique` or `dribble` therefore comes from the player's
    position and age and from nothing else.
    """
    return {p.id: replace(_BASE_STATS, player_id=p.id, unsupplied=unsupplied) for p in SQUAD}


def _rate_squad(mapper, attribute, unsupplied=()):
    """Every player's final rating for one attribute, in `SQUAD` order."""
    all_stats = _squad_stats(unsupplied)
    percentiles = mapper._compute_percentiles(all_stats)
    return [getattr(mapper.map_player(p, all_stats[p.id], percentiles), attribute) for p in SQUAD]


# --- the table that connects a category to the fields it reads ---


def test_every_category_the_mapper_percentiles_declares_its_inputs(mapper):
    # The categories are built inside `_compute_percentiles`, so this runs the
    # real thing rather than restating the list. A category present there and
    # absent from `CATEGORY_INPUTS` raises `KeyError` on the live patch path; one
    # present here and absent there is a rule that can never fire.
    computed = mapper._compute_percentiles(_squad_stats())
    assert set(computed) == set(StatMapper.CATEGORY_INPUTS)


def test_every_declared_input_is_a_field_that_exists_on_player_stats():
    # A misspelt input name can never be found in `unsupplied`, so the category
    # goes on being rated from filler and the bug looks fixed.
    declared = {f.name for f in fields(PlayerStats)}
    named = {name for inputs in StatMapper.CATEGORY_INPUTS.values() for name in inputs}
    assert (named - declared) == set()


def test_the_three_categories_ESPN_cannot_supply_are_exactly_the_estimated_ones():
    # The claim the whole round rests on: these three categories, and only these
    # three, read a field ESPN does not measure.
    orphaned = {
        category
        for category, inputs in StatMapper.CATEGORY_INPUTS.items()
        if set(inputs) & set(ABSENT_FROM_ESPN)
    }
    assert orphaned == {"body_balance", "technique", "dribble"}


# --- the collapse this round exists to fix ---


def test_an_undeclared_absence_puts_the_whole_squad_on_the_floor(mapper):
    # The bug, reproduced. Every record carries `0` for duels and dribbles and
    # says nothing about it, so every raw value ties at zero, nobody is below
    # anybody, and the percentile is 0 for all seven — the minimum rating, for
    # every player in the league. `technique` reads 2 rather than 1 only because
    # `_apply_position_adjustments` hands midfielders a +1 afterwards.
    assert _rate_squad(mapper, "body_balance") == [1, 1, 1, 1, 1, 1, 1]
    assert _rate_squad(mapper, "dribble") == [1, 1, 1, 1, 1, 1, 1]
    assert _rate_squad(mapper, "technique") == [1, 1, 1, 2, 2, 1, 1]


def test_a_declared_absence_rates_the_squad_from_position_and_age(mapper):
    # The fix. Same seven records, same zeros, one difference: they say the zeros
    # are filler. Concrete values, not a range: `_estimate_body_balance` bases
    # goalkeepers and defenders at 6 and everyone else at 5, adds one for the
    # 25-30 peak and takes one off under 21 and over 33.
    assert _rate_squad(mapper, "body_balance", ABSENT_FROM_ESPN) == [6, 7, 5, 4, 5, 6, 5]
    # `_estimate_dribble`: 3 for goalkeepers and defenders, 6 for the rest, plus
    # one under 27 and minus one over 32.
    assert _rate_squad(mapper, "dribble", ABSENT_FROM_ESPN) == [4, 3, 2, 7, 6, 7, 5]
    # `_estimate_technique`: 4 and 6, minus one under 23 and plus one from 31,
    # and then the midfielders' +1 from `_apply_position_adjustments` on top.
    assert _rate_squad(mapper, "technique", ABSENT_FROM_ESPN) == [3, 4, 5, 6, 8, 6, 7]


def test_the_declared_absence_is_what_produces_the_spread(mapper):
    # The counts the round is measured by, asserted rather than implied. A test
    # that only checked the ratings were in 1..9 would pass on the floored squad
    # above, which is the failure mode this whole file is written against.
    assert len(set(_rate_squad(mapper, "body_balance"))) == 1
    assert len(set(_rate_squad(mapper, "body_balance", ABSENT_FROM_ESPN))) == 4
    assert len(set(_rate_squad(mapper, "dribble"))) == 1
    assert len(set(_rate_squad(mapper, "dribble", ABSENT_FROM_ESPN))) == 6
    assert len(set(_rate_squad(mapper, "technique"))) == 2
    assert len(set(_rate_squad(mapper, "technique", ABSENT_FROM_ESPN))) == 6


def test_the_attributes_that_were_never_broken_are_untouched_by_the_fix(mapper):
    # `offensive` and `defensive` read only fields ESPN does supply, and were
    # already correct — which is why the collapse went unnoticed. Declaring the
    # duel and dribble absences must not disturb them. All seven records are
    # identical, so every player ties and percentiles to the floor; what varies
    # is `_apply_position_adjustments`, which gives the goalkeeper +2 defensive
    # and the two defenders +1.
    assert _rate_squad(mapper, "offensive") == _rate_squad(mapper, "offensive", ABSENT_FROM_ESPN)
    assert _rate_squad(mapper, "defensive") == _rate_squad(mapper, "defensive", ABSENT_FROM_ESPN)
    assert _rate_squad(mapper, "defensive", ABSENT_FROM_ESPN) == [3, 2, 2, 1, 1, 1, 1]


# --- a real zero is still a real zero ---


def test_a_player_who_genuinely_won_no_duels_still_rates_at_the_floor(mapper):
    # The other half of the distinction. The fix must not launder a measured zero
    # into a comfortable position-based guess: this player was in 310 duels and
    # won none of them, his provider measured that, and he is the worst in the
    # league at it. Ten records rather than three, because the percentile is
    # `below / n` and the best of three is only the 67th — too near the middle of
    # the table to be a contrast worth asserting.
    measured = {
        pid: replace(_BASE_STATS, player_id=pid, duels_total=310, duels_won=(pid - 1) * 30)
        for pid in range(1, 11)
    }
    percentiles = mapper._compute_percentiles(measured)
    assert percentiles["body_balance"][1] == 0.0
    assert percentiles["body_balance"][10] == 90.0
    loser = mapper.map_player(SQUAD[0], measured[1], percentiles)
    winner = mapper.map_player(SQUAD[2], measured[10], percentiles)
    assert loser.body_balance == 1
    assert winner.body_balance == 8
    # And the estimator was not consulted for either of them: it would have said
    # 6 for this goalkeeper and 5 for this defender, which is neither answer.
    assert mapper._estimate_body_balance(SQUAD[0]) == 6
    assert mapper._estimate_body_balance(SQUAD[2]) == 5


def test_an_unmeasured_player_is_left_out_of_the_ranking_and_not_ranked_last(mapper):
    # Exclusion, not a zero: a record that does not measure duels must not appear
    # in the duel ranking at all, or it is a non-answer competing with answers —
    # and in a league where only some records lack the stat it drags every
    # measured player's percentile up by sitting below them.
    mixed = {
        1: replace(_BASE_STATS, player_id=1, duels_total=310, duels_won=100),
        2: replace(_BASE_STATS, player_id=2, duels_total=310, duels_won=200),
        3: replace(_BASE_STATS, player_id=3, unsupplied=ABSENT_FROM_ESPN),
    }
    percentiles = mapper._compute_percentiles(mixed)
    assert sorted(percentiles["body_balance"]) == [1, 2]
    # Two ranked players, so the better of them is above one of two: 50%, not the
    # 66.7% that a third body in the population would have given him.
    assert percentiles["body_balance"] == {1: 0.0, 2: 50.0}
    # And the categories he does measure keep him in.
    assert sorted(percentiles["offensive"]) == [1, 2, 3]


# --- the estimators themselves ---


# Attribute, its estimator, and an age that sits in none of that estimator's
# adjustment bands — 23 is neither under 21 nor 25-to-30, 25 is neither under 23
# nor over 30, 29 is neither under 27 nor over 32. No single age is neutral for
# all three, which is itself the point: each carries its own age curve.
_NEUTRAL_AGES = [
    ("body_balance", "_estimate_body_balance", 23),
    ("technique", "_estimate_technique", 25),
    ("dribble", "_estimate_dribble", 29),
]


@pytest.mark.parametrize("position", ["Goalkeeper", "Defender", "Midfielder", "Attacker"])
@pytest.mark.parametrize(("attribute", "estimator", "age"), _NEUTRAL_AGES)
def test_an_estimator_agrees_with_the_no_stats_fallback_where_age_says_nothing(
    mapper, position, attribute, estimator, age
):
    # Two paths lead to "this was not measured": no record at all, which
    # `_fallback_attributes` answers, and a record whose provider does not
    # measure this, which the estimators answer. They must not disagree about the
    # same player for no reason, so each estimator's position base is the number
    # `FALLBACK_ATTRS` already used.
    player = Player(id=1, name="X", position=position, age=age)
    assert getattr(mapper, estimator)(player) == StatMapper.FALLBACK_ATTRS[position][attribute]


def test_the_technique_estimator_reproduces_the_fallback_age_rule_exactly(mapper):
    # `_fallback_attributes` already adjusts `technique` for age; the estimator
    # must not invent a second, different curve for the same attribute. Every
    # band, including the two the fallback treats alike.
    for age in (18, 22, 23, 24, 30, 31, 33, 34, 39):
        player = Player(id=1, name="X", position="Attacker", age=age)
        assert mapper._estimate_technique(player) == mapper._fallback_attributes(player).technique


def test_an_unknown_position_still_gets_a_rating_from_every_estimator(mapper):
    # `_parse_squad` maps everything it does not recognise to "Midfielder", but
    # `map_player` is public and a caller can hand over anything. The `.get`
    # defaults are what keep that a number rather than a `KeyError`.
    player = Player(id=1, name="X", position="Sweeper", age=24)
    assert mapper._estimate_body_balance(player) == 5
    assert mapper._estimate_technique(player) == 5
    assert mapper._estimate_dribble(player) == 6


def test_an_estimate_is_clamped_into_the_roms_one_to_nine_scale(mapper):
    # The ROM stores a nibble-scale value and `_apply_position_adjustments`
    # clamps afterwards, but an estimator that returned 0 for a teenage
    # goalkeeper would be a bug in the estimator, not something to be papered
    # over downstream.
    teenager = Player(id=1, name="X", position="Midfielder", age=17)
    assert mapper._estimate_body_balance(teenager) == 4
    veteran = Player(id=2, name="Y", position="Goalkeeper", age=40)
    assert mapper._estimate_dribble(veteran) == 2


def test_a_player_with_no_stats_at_all_still_takes_the_untouched_fallback(mapper):
    # The estimators are reached from `_rate`, which only runs for a player who
    # has a record. A player with none takes `_fallback_attributes`, which is
    # ported code this round does not move.
    attrs = mapper.map_player(SQUAD[3], None, {})
    assert attrs.body_balance == 5
    assert attrs.dribble == 6
    assert attrs.technique == 5


# --- through the mapper's own entry point ---


def test_a_whole_squad_mapped_with_absent_duel_data_is_not_uniformly_clumsy(mapper):
    # `map_team_with_league_context` is what `map_rosters` calls, and it is where
    # the collapse actually reached the ROM. Asserting the multiset of ratings
    # rather than a per-player list, because `_select_best_22` reorders.
    all_stats = _squad_stats(ABSENT_FROM_ESPN)
    roster = TeamRoster(
        team=Team(id=100, name="Utd", code="UTD"), players=SQUAD, player_stats=all_stats
    )
    record = mapper.map_team_with_league_context(roster, [roster])
    assert sorted(p.attributes.body_balance for p in record.players) == [4, 5, 5, 5, 6, 6, 7]
    assert sorted(p.attributes.dribble for p in record.players) == [2, 3, 4, 5, 6, 7, 7]
    assert sorted(p.attributes.technique for p in record.players) == [3, 4, 5, 6, 6, 7, 8]
