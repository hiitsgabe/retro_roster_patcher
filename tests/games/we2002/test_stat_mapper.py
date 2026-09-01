"""Turning a provider `Player` into the two name fields the ROM stores.

`_format_player_name` is the one place a provider string becomes ROM bytes
without passing through a numeric mapping, so it is where a malformed payload
gets to decide whether the whole patch runs. It has three fallbacks — display
name to last name, and forename to the display name's first word — and each of
them was reached by testing a string for truthiness, which a string of spaces
passes.
"""

import pytest

from retro_roster_patcher.games.we2002.stat_mapper import StatMapper
from retro_roster_patcher.sports.models import Player, Team, TeamRoster


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
