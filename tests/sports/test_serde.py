import json

import pytest

from retro_roster_patcher.sports.models import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)
from retro_roster_patcher.sports.serde import league_data_from_dict, league_data_to_dict


def stats(player_id: int) -> PlayerStats:
    """`PlayerStats` declares every field but `lineups` as required, so spell them all out."""
    return PlayerStats(
        player_id=player_id,
        appearances=30,
        minutes=2450,
        goals=4,
        assists=3,
        shots_total=21,
        shots_on=9,
        passes_total=1580,
        passes_accuracy=88.4,
        tackles_total=64,
        interceptions=31,
        blocks=12,
        duels_total=310,
        duels_won=171,
        dribbles_attempts=22,
        dribbles_success=11,
        fouls_committed=41,
        fouls_drawn=28,
        cards_yellow=7,
        cards_red=1,
        rating=7.1,
        lineups=28,
    )


def sample() -> LeagueData:
    return LeagueData(
        league=League(id=39, name="Premier League", country="England", season=2024),
        teams=[
            TeamRoster(
                team=Team(id=33, name="Manchester United", code="MUN", color="DA291C"),
                players=[
                    Player(id=18, name="Casemiro", position="Midfielder", number=18),
                    Player(id=19, name="Onana", position="Goalkeeper", number=24),
                ],
                player_stats={18: stats(18)},
                # The real shape. Both producers of this blob key it by `str`
                # player id -- `espn.py`'s `_extract_pid` returns `str | None`,
                # `nhl.py` does `str(sk.get("playerId", ""))` -- and the consumer
                # reads it back as `leaders.get(str(player.id), {})`. A blob keyed
                # by `int` would not survive JSON, so using the real shape here is
                # what makes the pass-through claim meaningful.
                extra={"leaders": {"8471675": {"G": 42, "A": 54, "PTS": 96}}},
            ),
            # `loading` and `error` sit at their dataclass defaults on the roster
            # above, so nothing there can tell whether the reader sets them at
            # all. This one is in a non-default state for both, and gives the
            # team list a length and an order worth asserting.
            TeamRoster(
                team=Team(id=40, name="Liverpool", code="LIV"),
                loading=True,
                error="rate limited",
            ),
        ],
    )


def round_trip(data: LeagueData) -> LeagueData:
    """Through real JSON, which is the only trip that matters."""
    return league_data_from_dict(json.loads(json.dumps(league_data_to_dict(data))))


def test_a_league_survives_a_full_json_round_trip():
    original = sample()
    assert round_trip(original) == original


def test_player_stats_keys_come_back_as_ints():
    restored = round_trip(sample())
    assert list(restored.teams[0].player_stats) == [18]
    # The value comparison above is not the claim, and on its own cannot be:
    # `[18.0] == [18]` is `True` and `hash(18.0) == hash(18)`, so neither it nor
    # a whole-object `restored == original` can tell an `int` key from a `float`
    # one. `TeamRoster.player_stats` is declared `dict[int, PlayerStats]`, so the
    # type is what has to be asserted.
    assert type(list(restored.teams[0].player_stats)[0]) is int
    assert type(restored.teams[0].player_stats[18]) is PlayerStats
    # And the divergence is not academic: it lands in the file itself. `str(18)`
    # is `"18"` but `str(18.0)` is `"18.0"`, so a key of the wrong numeric type
    # rewrites the key text of the rosters file that `fetch` hands to `patch`.
    assert '"player_stats": {"18": ' in json.dumps(league_data_to_dict(restored))


def test_the_extra_blob_passes_through_untouched():
    restored = round_trip(sample())
    assert restored.teams[0].extra == {"leaders": {"8471675": {"G": 42, "A": 54, "PTS": 96}}}


def test_extra_keys_are_passed_through_rather_than_re_keyed():
    # The comment beside `serde.py`'s `int(pid)` conversion claims `extra` "needs
    # no equivalent conversion". The fixture cannot test it: its `extra` is `str`-
    # keyed already -- the real shape, and worth keeping for that -- so a reader
    # that coerced every key with `str()` would satisfy it unchanged.
    #
    # So skip the `json.dumps` hop and feed `league_data_to_dict` output straight
    # back in. That is the only path on which a non-`str` key exists to be
    # observed at all; through real JSON every key is a `str` before the reader
    # ever sees it, which is exactly why `player_stats` needs its conversion and
    # this one must not have one.
    data = LeagueData(
        league=League(id=1, name="N"),
        teams=[TeamRoster(team=Team(id=1, name="X"), extra={7: "seven"})],
    )
    restored = league_data_from_dict(league_data_to_dict(data))
    assert list(restored.teams[0].extra) == [7]
    assert type(list(restored.teams[0].extra)[0]) is int


def test_the_extra_blob_is_copied_rather_than_aliased():
    # `extra` is the only field handed over whole instead of rebuilt from its
    # parts, so it is the only one that can end up sharing an object with the
    # payload it was read from. Without the `dict(...)` around it, a write through
    # the roster reaches back into the caller's parsed JSON, which is a mutation
    # of an argument this function was only asked to read.
    source = {"leaders": {"8471675": {"G": 42}}}
    raw = {
        "league": {"id": 1, "name": "N"},
        "teams": [{"team": {"id": 1, "name": "X"}, "extra": source}],
    }
    restored = league_data_from_dict(raw)
    restored.teams[0].extra["POISON"] = 1
    assert source == {"leaders": {"8471675": {"G": 42}}}


def test_a_loading_roster_keeps_its_state_and_its_place_in_the_list():
    restored = round_trip(sample())
    assert [t.team.code for t in restored.teams] == ["MUN", "LIV"]
    assert restored.teams[1].loading is True
    assert restored.teams[1].error == "rate limited"
    assert restored.teams[0].loading is False
    assert restored.teams[0].error == ""


def test_unknown_keys_are_ignored_so_newer_files_still_load():
    raw = league_data_to_dict(sample())
    raw["league"]["some_future_field"] = "whatever"
    raw["teams"][0]["team"]["stadium"] = "Old Trafford"
    # All four nested types filter independently, so plant a future key in each.
    # `player_stats` is still keyed by `int` here: this is `league_data_to_dict`
    # output that has not been through `json.dumps`.
    raw["teams"][0]["players"][0]["preferred_foot"] = "right"
    raw["teams"][0]["player_stats"][18]["expected_goals"] = 3.7
    restored = league_data_from_dict(raw)
    assert restored.league.name == "Premier League"
    assert restored.teams[0].team.name == "Manchester United"
    assert restored.teams[0].players[0].name == "Casemiro"
    assert restored.teams[0].player_stats[18] == stats(18)


def test_optional_keys_may_be_absent_entirely():
    # A hand-written file, not one this module produced: every optional key of a
    # roster is missing at once, which no `league_data_to_dict` output ever is.
    #
    # Absent is only one of the two shapes the `or []` / `or {}` guards absorb.
    # `test_a_present_json_null_is_absorbed_like_an_absent_key` is the other, and
    # it is the one that separates `X or D` from `raw.get(k, D)` -- those two
    # agree on everything this test does.
    raw = {
        "league": {"id": 39, "name": "Premier League"},
        "teams": [{"team": {"id": 1, "name": "X"}}],
    }
    restored = league_data_from_dict(raw)
    assert restored.league.country == ""
    assert restored.league.season == 0
    assert restored.teams[0].players == []
    assert restored.teams[0].player_stats == {}
    assert restored.teams[0].extra == {}
    assert restored.teams[0].loading is False
    assert restored.teams[0].error == ""


def test_absent_containers_are_read_as_empty():
    # The three guards the test above does not reach: `league`, `teams`, and a
    # roster's own `team`. Absent is not the same as empty in any of the three,
    # but the failure mode is not the same either, so the assertions below are
    # not either.
    #
    # `league` and `team` are handed to `_only_declared`. Unguarded, `raw.get`
    # yields `None` and `_only_declared` calls `None.items()`, which is
    # `AttributeError: 'NoneType' object has no attribute 'items'` -- not the
    # `TypeError` naming the `id` and `name` the file actually lacks. Those two get
    # a `pytest.raises` for that `TypeError`.
    #
    # `teams` never reaches `_only_declared`: it is the iterable of a list
    # comprehension. Unguarded it is `TypeError: 'NoneType' object is not iterable`
    # raised at the comprehension itself, so there is no wrong-exception contrast to
    # draw and the guarded result -- an empty list -- is the whole claim.
    assert league_data_from_dict({"league": {"id": 1, "name": "N"}}).teams == []
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"teams": []})
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"league": {"id": 1, "name": "N"}, "teams": [{}]})


def test_a_present_json_null_is_absorbed_like_an_absent_key():
    # The distinction the two tests above do not draw. `null` is legal JSON and a
    # writer that emits every key -- an older schema, a hand edit, a Dart
    # `jsonEncode` of a nullable field -- sends it where this one omits the key.
    # Every guard on the read side is `raw.get(k) or DEFAULT` rather than
    # `raw.get(k, DEFAULT)` for exactly that reason: the two forms are identical
    # on an absent key and differ only on a present `null`. Nothing above can
    # therefore tell the six `or`s from a `get` default; this payload can.
    #
    # Built by parsing JSON text rather than as Python dicts, so what is claimed
    # legal here is demonstrably what a file can carry.
    roster = json.loads(
        '{"league": {"id": 1, "name": "N"},'
        ' "teams": [{"team": {"id": 1, "name": "X"},'
        ' "players": null, "player_stats": null, "extra": null}]}'
    )
    restored = league_data_from_dict(roster)
    assert restored.teams[0].players == []
    assert restored.teams[0].player_stats == {}
    assert restored.teams[0].extra == {}
    # `teams` is the iterable of a comprehension: a `null` reaching it is a
    # `TypeError` at the comprehension, not a field holding `None`, so the empty
    # list is again the whole claim.
    nulled_teams = json.loads('{"league": {"id": 1, "name": "N"}, "teams": null}')
    assert league_data_from_dict(nulled_teams).teams == []
    # `league` and a roster's own `team` go to `_only_declared`, which calls
    # `.items()`. Absorbing the `null` into `{}` is what turns an
    # `AttributeError: 'NoneType' object has no attribute 'items'` into the
    # `TypeError` that names the `id` and `name` the payload actually lacks.
    nulled_league = json.loads('{"league": null, "teams": []}')
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict(nulled_league)
    nulled_team = json.loads('{"league": {"id": 1, "name": "N"}, "teams": [{"team": null}]}')
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict(nulled_team)


def test_json_legal_values_of_the_wrong_type_are_coerced_to_the_declared_one():
    # Leniency about optional fields extends to their type. A hand-edited or
    # older-schema file can legally carry `1` for `loading` and a bare number for
    # `error` -- both are valid JSON, neither is the declared `bool` / `str`. The
    # `bool()` and `str()` calls on the read side are the only thing standing
    # between those and a typed field holding something it does not declare.
    #
    # Coercion is also the *whole* of what the read side does to the value. The
    # second roster's `error` is already a `str`, and a number cannot carry the
    # thing that shows it is passed through verbatim, so it carries the
    # surrounding whitespace a provider message plausibly arrives with: it comes
    # back as written rather than tidied.
    raw = {
        "league": {"id": 1, "name": "N"},
        "teams": [
            {"team": {"id": 1, "name": "X"}, "loading": 1, "error": 404},
            {"team": {"id": 2, "name": "Y"}, "error": "  rate limited\n"},
        ],
    }
    restored = league_data_from_dict(raw)
    assert restored.teams[0].loading is True
    assert restored.teams[0].error == "404"
    assert restored.teams[1].error == "  rate limited\n"


def test_a_payload_missing_a_required_field_raises():
    # Leniency stops at fields with defaults. `id` and `name` have none, so a
    # truncated file is an error rather than a silently half-built league.
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"league": {"id": 39}, "teams": []})


def test_an_empty_roster_round_trips():
    empty = LeagueData(league=League(id=1, name="NHL"), teams=[])
    assert round_trip(empty) == empty
