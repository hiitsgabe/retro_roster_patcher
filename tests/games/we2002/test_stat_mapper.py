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
a spot check look fine. `PlayerStats.unsupplied` is what tells the two apart --
and this mapper does not read it.

That is upstream's behaviour, it is wrong, and it is preserved deliberately.
ESPN is this game's only provider and it measures neither duels nor dribbles, so
`body_balance`, `technique` and `dribble` are the floor rating for every player
in every patched ISO. A `CATEGORY_INPUTS` table and three position-and-age
estimators fixed exactly that and have been removed: they wrote bytes no
released build of this patcher ever produced, nothing in this repository has
been validated against a real disc, and fidelity to the original beats
correctness of the data. Measured, the gating moved 1 251 bytes across 32 Master
League slots and made 150 of 150 synthetic leagues differ from upstream.

The tests below therefore pin the collapse, and each one that does says so in
its own docstring. Do not "fix" them.
"""

from dataclasses import replace

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

    Identical on purpose: with every raw value tied, the percentile below any
    of them is zero, so anything the tests below observe in `body_balance`,
    `technique` or `dribble` is the floor rating and nothing else.
    """
    return {p.id: replace(_BASE_STATS, player_id=p.id, unsupplied=unsupplied) for p in SQUAD}


def _rate_squad(mapper, attribute, unsupplied=()):
    """Every player's final rating for one attribute, in `SQUAD` order."""
    all_stats = _squad_stats(unsupplied)
    percentiles = mapper._compute_percentiles(all_stats)
    return [getattr(mapper.map_player(p, all_stats[p.id], percentiles), attribute) for p in SQUAD]


# --- what a declared absence does, which is nothing ---


def test_a_provider_that_measures_no_duels_puts_the_whole_squad_on_the_floor(mapper):
    """PINS UPSTREAM FIDELITY DELIBERATELY, and it is known to be wrong.

    Every record carries `0` for duels and dribbles, every raw value therefore
    ties at zero, nobody is below anybody, and the percentile is 0 for all seven
    -- the minimum rating, for every player in the league. `technique` reads 2
    rather than 1 for the two midfielders only because
    `_apply_position_adjustments` hands them a +1 afterwards.

    ESPN is this game's only provider and it measures none of these fields, so
    this is what every patched ISO gets: three of fifteen attributes constant
    across the whole game. The mapper knows better -- `PlayerStats.unsupplied`
    names those four fields as filler -- and does not act on it, because acting
    on it writes bytes no released build of this patcher ever produced and
    nothing here has been validated against a real disc.
    """
    assert _rate_squad(mapper, "body_balance") == [1, 1, 1, 1, 1, 1, 1]
    assert _rate_squad(mapper, "dribble") == [1, 1, 1, 1, 1, 1, 1]
    assert _rate_squad(mapper, "technique") == [1, 1, 1, 2, 2, 1, 1]


@pytest.mark.parametrize("attribute", ["body_balance", "dribble", "technique"])
def test_declaring_the_absence_changes_no_rating(mapper, attribute):
    # The same seven records, the same zeros, one difference: they now say the
    # zeros are filler. It makes no difference to a single byte, which is the
    # whole content of the revert. A position-and-age estimator behind
    # `unsupplied` used to make all three of these spread out.
    assert _rate_squad(mapper, attribute, ABSENT_FROM_ESPN) == _rate_squad(mapper, attribute)


@pytest.mark.parametrize("absent_field", list(ABSENT_FROM_ESPN))
def test_declaring_one_field_absent_changes_no_rating_either(mapper, absent_field):
    # One name at a time, because a test that declares all four at once cannot
    # show that no single one of them is consulted.
    assert _rate_squad(mapper, "body_balance", (absent_field,)) == [1, 1, 1, 1, 1, 1, 1]


def test_the_three_collapsed_attributes_are_exactly_the_ones_espn_cannot_supply(mapper):
    # The cost, named. Of the ten percentiled categories these three read a field
    # ESPN does not measure, and they are the three that come out constant; the
    # other seven vary, so the collapse is invisible in a spot check of a patched
    # ISO. Recorded here so the size of the defect is written down somewhere.
    collapsed = {
        attribute
        for attribute in ("body_balance", "dribble", "technique", "offensive", "defensive")
        if len(set(_rate_squad(mapper, attribute, ABSENT_FROM_ESPN))) == 1
    }
    assert collapsed == {"body_balance", "dribble"}
    # `technique` is not in the set only because of the midfielders' +1; it is
    # still two values across seven players, and both come from a floored
    # percentile rather than from anything measured.
    assert len(set(_rate_squad(mapper, "technique", ABSENT_FROM_ESPN))) == 2


def test_the_attributes_espn_does_supply_are_unaffected_by_the_declaration(mapper):
    # `offensive` and `defensive` read only fields ESPN does supply, so nothing
    # about `unsupplied` could reach them either way. All seven records are
    # identical, so every player ties and percentiles to the floor; what varies
    # is `_apply_position_adjustments`, which gives the goalkeeper +2 defensive
    # and the two defenders +1.
    assert _rate_squad(mapper, "offensive") == _rate_squad(mapper, "offensive", ABSENT_FROM_ESPN)
    assert _rate_squad(mapper, "defensive") == _rate_squad(mapper, "defensive", ABSENT_FROM_ESPN)
    assert _rate_squad(mapper, "defensive", ABSENT_FROM_ESPN) == [3, 2, 2, 1, 1, 1, 1]


# --- a measured zero, and the unmeasured one it is indistinguishable from ---


def test_a_player_who_genuinely_won_no_duels_rates_at_the_floor(mapper):
    # The half that was never wrong: this player was in 310 duels and won none of
    # them, his provider measured that, and he is the worst in the league at it.
    # Ten records rather than three, because the percentile is `below / n` and
    # the best of three is only the 67th -- too near the middle of the table to
    # be a contrast worth asserting.
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


def test_an_unmeasured_player_is_ranked_below_every_measured_one(mapper):
    """PINS UPSTREAM FIDELITY DELIBERATELY, and it is known to be wrong.

    Player 3's provider did not measure duels, and he is ranked on the filler
    zero anyway: bottom of the table, indistinguishable from the player above
    who was measured and won none. He also counts towards `n`, so in this
    mixed-provider league the two measured players are percentiled against a
    population of three rather than two and the better of them reads 66.7%
    instead of 50%. Leaving him out was tried and is reverted -- excluding him
    changes the ratings of players who *were* measured, which is a change to the
    bytes for every one of them.
    """
    mixed = {
        1: replace(_BASE_STATS, player_id=1, duels_total=310, duels_won=100),
        2: replace(_BASE_STATS, player_id=2, duels_total=310, duels_won=200),
        3: replace(_BASE_STATS, player_id=3, unsupplied=ABSENT_FROM_ESPN),
    }
    percentiles = mapper._compute_percentiles(mixed)
    assert sorted(percentiles["body_balance"]) == [1, 2, 3]
    assert percentiles["body_balance"] == {1: 33.33333333333333, 2: 66.66666666666666, 3: 0.0}


# --- the estimators that remain ---


@pytest.mark.parametrize(
    ("estimator", "expected"),
    [
        # Goalkeeper, Defender, Midfielder, Attacker, in that order.
        ("_estimate_jump", [7, 6, 5, 5]),
        ("_estimate_heading", [5, 6, 5, 5]),
        ("_estimate_curve", [3, 3, 5, 5]),
    ],
)
def test_the_surviving_estimators_keep_their_position_tables(mapper, estimator, expected):
    # Five attributes have no statistic behind them at all in either tree --
    # speed, acceleration, jump power, heading and curve -- and are estimated
    # from position and age. Those five are upstream's own and are untouched;
    # the three that estimated `body_balance`, `technique` and `dribble` were
    # this port's and are gone.
    positions = ["Goalkeeper", "Defender", "Midfielder", "Attacker"]
    rated = [
        getattr(mapper, estimator)(Player(id=1, name="X", position=p, age=26)) for p in positions
    ]
    assert rated == expected


def test_the_speed_estimator_carries_its_age_curve(mapper):
    # Concrete numbers, because a range would pass on a constant. Attacker base
    # 6, plus one under 25 and minus one over 32.
    assert mapper._estimate_speed(Player(id=1, name="X", position="Attacker", age=22)) == 7
    assert mapper._estimate_speed(Player(id=2, name="Y", position="Attacker", age=28)) == 6
    assert mapper._estimate_speed(Player(id=3, name="Z", position="Attacker", age=35)) == 5


def test_an_unknown_position_still_gets_a_rating_from_every_estimator(mapper):
    # `_parse_squad` maps everything it does not recognise to "Midfielder", but
    # `map_player` is public and a caller can hand over anything. The `.get`
    # defaults are what keep that a number rather than a `KeyError`.
    player = Player(id=1, name="X", position="Sweeper", age=24)
    assert mapper._estimate_speed(player) == 6
    assert mapper._estimate_jump(player) == 5
    assert mapper._estimate_heading(player) == 5
    assert mapper._estimate_curve(player) == 4


def test_a_player_with_no_stats_at_all_takes_the_fallback_attributes(mapper):
    # A player with no record at all is the one case that never reaches a
    # percentile: `map_player` returns `_fallback_attributes` outright, which is
    # ported code and carries its own position table.
    attrs = mapper.map_player(SQUAD[3], None, {})
    assert attrs.body_balance == 5
    assert attrs.dribble == 6
    assert attrs.technique == 5


# --- through the mapper's own entry point ---


def test_a_whole_squad_mapped_with_absent_duel_data_is_uniformly_clumsy(mapper):
    """PINS UPSTREAM FIDELITY DELIBERATELY, and it is known to be wrong.

    `map_team_with_league_context` is what `map_rosters` calls, so this is the
    shape that reaches the ISO. Asserting the multiset rather than a per-player
    list, because `_select_best_22` reorders.
    """
    all_stats = _squad_stats(ABSENT_FROM_ESPN)
    roster = TeamRoster(
        team=Team(id=100, name="Utd", code="UTD"), players=SQUAD, player_stats=all_stats
    )
    record = mapper.map_team_with_league_context(roster, [roster])
    assert sorted(p.attributes.body_balance for p in record.players) == [1, 1, 1, 1, 1, 1, 1]
    assert sorted(p.attributes.dribble for p in record.players) == [1, 1, 1, 1, 1, 1, 1]
    assert sorted(p.attributes.technique for p in record.players) == [1, 1, 1, 1, 1, 2, 2]
